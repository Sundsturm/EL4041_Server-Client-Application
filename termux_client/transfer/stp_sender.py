"""
transfer/stp_sender.py
STP/TCP sender for Termux (owner side).

Wire format — matches desktop stp_provider.py / stp_downloader.py:
    [4-byte FRAME_LEN big-endian uint32]   = len(json_bytes) + len(payload)
    [4-byte JSON_LEN  big-endian uint32]   = len(json_bytes)
    [JSON_LEN bytes   UTF-8 JSON header]
    [FRAME_LEN - JSON_LEN bytes  binary payload]

Retry logic is built-in for connection-refused errors: the downloader
may not have its listener ready yet when the owner approves (race
condition). We retry up to MAX_CONNECT_ATTEMPTS times with exponential
backoff before giving up.
"""

from __future__ import annotations

import json
import socket
import struct
import time
from pathlib import Path

from config import STP_CHUNK_SIZE
from transfer.integrity import sha256_file, sha256_bytes


# ---------------------------------------------------------------------------
# Frame helpers (compatible with desktop stp_provider.py / stp_downloader.py)
# ---------------------------------------------------------------------------

def _build_frame(header: dict, payload: bytes = b"") -> bytes:
    json_bytes = json.dumps(header).encode("utf-8")
    json_len   = len(json_bytes)
    frame_len  = json_len + len(payload)
    return struct.pack(">II", frame_len, json_len) + json_bytes + payload


def _recv_frame(sock: socket.socket) -> dict:
    """Read one frame from sock; returns the JSON header dict."""
    prefix = _recv_exact(sock, 8)
    frame_len, json_len = struct.unpack(">II", prefix)
    json_bytes = _recv_exact(sock, json_len)
    _payload   = _recv_exact(sock, frame_len - json_len)   # consumed but not returned
    return json.loads(json_bytes.decode("utf-8"))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(
                f"Connection closed after {len(buf)}/{n} bytes."
            )
        buf.extend(chunk)
    return bytes(buf)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_file_to_peer(
    peer_ip: str,
    peer_port: int,
    file_path: str | Path,
    music_id: str,
    peer_token: str,
    mime_type: str = "application/octet-stream",
) -> None:
    path         = Path(file_path)
    size         = path.stat().st_size
    total_chunks = (size + STP_CHUNK_SIZE - 1) // STP_CHUNK_SIZE
    file_hash    = sha256_file(path)

    # ── Connect with retry (race-condition guard) ──────────────────────────
    MAX_CONNECT_ATTEMPTS = 3
    CONNECT_RETRY_BASE_S = 2.0   # 2 s → 4 s → 8 s

    for attempt in range(1, MAX_CONNECT_ATTEMPTS + 1):
        try:
            _conn = socket.create_connection((peer_ip, peer_port), timeout=30)
            break
        except (ConnectionRefusedError, OSError) as exc:
            if attempt < MAX_CONNECT_ATTEMPTS:
                wait = CONNECT_RETRY_BASE_S * (2 ** (attempt - 1))
                print(
                    f"[STP] connect attempt {attempt}/{MAX_CONNECT_ATTEMPTS} failed "
                    f"({exc}). Retrying in {int(wait)}s…"
                )
                time.sleep(wait)
            else:
                raise ConnectionRefusedError(
                    f"Could not connect to {peer_ip}:{peer_port} after "
                    f"{MAX_CONNECT_ATTEMPTS} attempts. "
                    f"Make sure the downloader is listening on that port. "
                    f"Last error: {exc}"
                ) from exc

    with _conn as conn:
        # ── 1. Send TRANSFER_REQ ───────────────────────────────────────────
        conn.sendall(_build_frame({
            "msg_type":     "TRANSFER_REQ",
            "peer_token":   peer_token,
            "music_id":     music_id,
            "filename":     path.name,
            "mime_type":    mime_type,
            "file_size":    size,
            "file_hash":    file_hash,
            "total_chunks": total_chunks,
            "chunk_size":   STP_CHUNK_SIZE,
        }))

        # ── 2. Wait for TRANSFER_ACCEPT ────────────────────────────────────
        conn.settimeout(10)
        try:
            accept = _recv_frame(conn)
            if accept.get("msg_type") == "TRANSFER_FAIL":
                raise RuntimeError(accept.get("reason", "transfer rejected"))
            if accept.get("msg_type") != "TRANSFER_ACCEPT":
                raise RuntimeError(
                    f"Expected TRANSFER_ACCEPT, got {accept.get('msg_type')}"
                )
        except socket.timeout:
            # Receiver did not send TRANSFER_ACCEPT — proceed anyway
            # (minimal receiver compatibility)
            pass
        finally:
            conn.settimeout(30)

        # ── 3. Send chunks ─────────────────────────────────────────────────
        with path.open("rb") as f:
            for chunk_id in range(total_chunks):
                chunk      = f.read(STP_CHUNK_SIZE)
                chunk_hash = sha256_bytes(chunk)
                is_last    = chunk_id == total_chunks - 1

                frame = _build_frame({
                    "msg_type":     "CHUNK_DATA",
                    "music_id":     music_id,
                    "chunk_id":     chunk_id,
                    "total_chunks": total_chunks,
                    "chunk_size":   len(chunk),
                    "chunk_hash":   chunk_hash,
                    "is_last":      is_last,
                }, payload=chunk)
                conn.sendall(frame)

                # Wait for ACK/NACK
                ack = _recv_frame(conn)
                if ack.get("msg_type") == "CHUNK_NACK":
                    # One retry
                    conn.sendall(frame)
                    ack = _recv_frame(conn)
                if ack.get("msg_type") != "CHUNK_ACK":
                    conn.sendall(_build_frame({
                        "msg_type": "TRANSFER_FAIL",
                        "music_id": music_id,
                        "reason":   "Expected CHUNK_ACK",
                    }))
                    raise RuntimeError("Transfer failed: ACK not received")

                print(f"[STP] sent chunk {chunk_id + 1}/{total_chunks}")

        # ── 4. Send TRANSFER_END ───────────────────────────────────────────
        conn.sendall(_build_frame({
            "msg_type": "TRANSFER_END",
            "music_id": music_id,
            "file_hash": file_hash,
        }))
        print("[STP] file sent successfully")
