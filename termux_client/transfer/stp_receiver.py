"""
transfer/stp_receiver.py
STP/TCP receiver for Termux (downloader side).

Wire format — matches desktop stp_provider.py / stp_downloader.py:
    [4-byte FRAME_LEN big-endian uint32]   = len(json_bytes) + len(payload)
    [4-byte JSON_LEN  big-endian uint32]   = len(json_bytes)
    [JSON_LEN bytes   UTF-8 JSON header]
    [FRAME_LEN - JSON_LEN bytes  binary payload]
"""

from __future__ import annotations

import json
import socket
import struct
from pathlib import Path

from config import STP_LISTEN_HOST, STP_LISTEN_PORT, DOWNLOAD_DIR
from transfer.integrity import sha256_bytes, sha256_file, hmac_sha256, hmac_sha256_file


# ---------------------------------------------------------------------------
# Frame helpers (compatible with desktop stp_provider.py / stp_downloader.py)
# ---------------------------------------------------------------------------

def _build_frame(header: dict, payload: bytes = b"") -> bytes:
    json_bytes = json.dumps(header).encode("utf-8")
    json_len   = len(json_bytes)
    frame_len  = json_len + len(payload)
    return struct.pack(">II", frame_len, json_len) + json_bytes + payload


def _recv_frame(sock: socket.socket) -> tuple[dict, bytes]:
    """Read one frame; returns (header_dict, payload_bytes)."""
    prefix = _recv_exact(sock, 8)
    frame_len, json_len = struct.unpack(">II", prefix)
    json_bytes = _recv_exact(sock, json_len)
    payload    = _recv_exact(sock, frame_len - json_len)
    return json.loads(json_bytes.decode("utf-8")), payload


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

def serve_once(
    host: str = STP_LISTEN_HOST,
    port: int = STP_LISTEN_PORT,
    accept_timeout: float = 300.0,   # 5 minutes: owner has this long to approve + connect
) -> Path | None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[STP] listening on {host}:{port} (timeout: {int(accept_timeout)}s)")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(1)
        server.settimeout(accept_timeout)

        try:
            conn, addr = server.accept()
        except socket.timeout:
            raise TimeoutError(
                f"No connection received within {int(accept_timeout)}s. "
                "Owner may not have approved in time. Please retry with 'download <id>'."
            )
        print(f"[STP] connected from {addr}")

        with conn:
            output_path: Path | None = None
            music_id     = ""
            peer_token   = ""
            expected_hash = ""

            while True:
                header, payload = _recv_frame(conn)
                msg_type = header.get("msg_type", "")

                # ── TRANSFER_REQ ───────────────────────────────────────────
                if msg_type == "TRANSFER_REQ":
                    music_id      = header.get("music_id", "")
                    peer_token    = header.get("peer_token", "")
                    filename      = header.get("filename", "downloaded_song.bin")
                    expected_hash = header.get("file_hash", "")
                    total_chunks  = int(header.get("total_chunks", 0))
                    chunk_size    = int(header.get("chunk_size", 0))

                    output_path = DOWNLOAD_DIR / filename
                    if output_path.exists():
                        output_path = DOWNLOAD_DIR / f"downloaded_{filename}"
                    output_path.write_bytes(b"")
                    print(f"[STP] receiving {filename} -> {output_path}")

                    # Send TRANSFER_ACCEPT (required by desktop stp_provider.py)
                    conn.sendall(_build_frame({
                        "msg_type":     "TRANSFER_ACCEPT",
                        "music_id":     music_id,
                        "filename":     output_path.name,
                        "resume_from":  0,
                        "chunk_size":   chunk_size,
                        "status":       "OK",
                    }))

                # ── CHUNK_DATA ─────────────────────────────────────────────
                elif msg_type == "CHUNK_DATA":
                    if output_path is None:
                        conn.sendall(_build_frame({
                            "msg_type": "TRANSFER_FAIL",
                            "music_id": music_id,
                            "reason":   "Received CHUNK_DATA before TRANSFER_REQ",
                        }))
                        return None

                    chunk_id   = int(header.get("chunk_id", 0))
                    chunk_hash = header.get("chunk_hash", "")
                    chunk_hmac = header.get("hmac", "")

                    if chunk_hash and sha256_bytes(payload) != chunk_hash:
                        conn.sendall(_build_frame({
                            "msg_type": "CHUNK_NACK",
                            "music_id": music_id,
                            "chunk_id": chunk_id,
                            "reason":   "chunk_hash mismatch",
                        }))
                        continue

                    if peer_token and chunk_hmac and hmac_sha256(peer_token, payload) != chunk_hmac:
                        conn.sendall(_build_frame({
                            "msg_type": "CHUNK_NACK",
                            "music_id": music_id,
                            "chunk_id": chunk_id,
                            "reason":   "chunk_hmac mismatch",
                        }))
                        continue

                    with output_path.open("ab") as f:
                        f.write(payload)

                    conn.sendall(_build_frame({
                        "msg_type": "CHUNK_ACK",
                        "music_id": music_id,
                        "chunk_id": chunk_id,
                    }))
                    print(f"[STP] received chunk {chunk_id}")

                # ── TRANSFER_END ───────────────────────────────────────────
                elif msg_type == "TRANSFER_END":
                    if output_path is None:
                        return None

                    final_hash = header.get("file_hash", expected_hash)
                    if final_hash:
                        actual_hash = sha256_file(output_path)
                        if actual_hash != final_hash:
                            raise RuntimeError("File hash mismatch after transfer")

                    final_hmac = header.get("hmac", "")
                    if peer_token and final_hmac:
                        actual_hmac = hmac_sha256_file(peer_token, output_path)
                        if actual_hmac != final_hmac:
                            raise RuntimeError("File HMAC mismatch after transfer")

                    print(f"[STP] transfer complete: {output_path}")
                    return output_path

                # ── TRANSFER_FAIL ──────────────────────────────────────────
                elif msg_type == "TRANSFER_FAIL":
                    raise RuntimeError(header.get("reason", "transfer failed"))
