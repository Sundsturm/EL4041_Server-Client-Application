"""
core/stp_receiver.py
STP (Song Transfer Protocol) — Receiver side.

Listens on a TCP port. Validates peer_token, receives chunks,
verifies SHA256 per chunk and whole file.
"""

import asyncio
import hashlib
import json
import os
import struct
from typing import Callable, Optional

from config import MUSIC_DIR, STP_LISTEN_PORT, STP_VERSION


def _chunk_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _build_frame(header: dict, payload: bytes = b"") -> bytes:
    json_bytes = json.dumps(header).encode("utf-8")
    json_len   = len(json_bytes)
    frame_len  = json_len + len(payload)
    prefix     = struct.pack(">II", frame_len, json_len)
    return prefix + json_bytes + payload


class STPReceiver:
    """
    TCP server that accepts one incoming STP transfer per connection.

    Usage
    ─────
    receiver = STPReceiver(
        valid_tokens={"TRX_991A"},
        progress_cb=lambda recv, total, music_id: ...,
        done_cb=lambda music_id, path: ...,
        error_cb=lambda music_id, reason: ...,
    )
    await receiver.start()   # non-blocking; runs in background
    await receiver.stop()
    """

    def __init__(
        self,
        valid_tokens: set,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
        done_cb:     Optional[Callable[[str, str], None]] = None,
        error_cb:    Optional[Callable[[str, str], None]] = None,
        listen_port: int = STP_LISTEN_PORT,
    ):
        self.valid_tokens = valid_tokens
        self.progress_cb  = progress_cb
        self.done_cb      = done_cb
        self.error_cb     = error_cb
        self.listen_port  = listen_port
        self._server: Optional[asyncio.Server] = None
        os.makedirs(MUSIC_DIR, exist_ok=True)

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_connection,
            host="0.0.0.0",
            port=self.listen_port,
        )
        asyncio.ensure_future(self._server.serve_forever())

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def add_valid_token(self, token: str):
        self.valid_tokens.add(token)

    def remove_valid_token(self, token: str):
        self.valid_tokens.discard(token)

    # ─── Frame helpers ───────────────────────────────────────────────────────

    async def _recv_frame(self, reader: asyncio.StreamReader) -> tuple[dict, bytes]:
        prefix     = await reader.readexactly(8)
        frame_len, json_len = struct.unpack(">II", prefix)
        json_bytes = await reader.readexactly(json_len)
        payload    = await reader.readexactly(frame_len - json_len)
        header     = json.loads(json_bytes.decode("utf-8"))
        return header, payload

    async def _send_frame(self, writer: asyncio.StreamWriter, header: dict):
        frame = _build_frame(header)
        writer.write(frame)
        await writer.drain()

    # ─── Connection handler ──────────────────────────────────────────────────

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        music_id = "unknown"
        tmp_path = ""
        try:
            # 1. Receive TRANSFER_REQ
            hdr, _ = await self._recv_frame(reader)
            if hdr.get("msg_type") != "TRANSFER_REQ":
                await self._send_frame(writer, {
                    "version": STP_VERSION, "msg_type": "TRANSFER_FAIL",
                    "reason": "expected TRANSFER_REQ",
                })
                return

            peer_token   = hdr.get("peer_token", "")
            music_id     = hdr.get("music_id", "unknown")
            filename     = hdr.get("filename", f"{music_id}.bin")
            total_chunks = hdr.get("total_chunks", 0)
            expected_file_hash = hdr.get("file_hash", "")

            # Validate token
            if peer_token not in self.valid_tokens:
                await self._send_frame(writer, {
                    "version": STP_VERSION, "msg_type": "TRANSFER_FAIL",
                    "reason": "invalid peer_token",
                })
                return

            # Check resume
            safe_filename = os.path.basename(filename)
            final_path    = os.path.join(MUSIC_DIR, safe_filename)
            tmp_path      = final_path + ".part"
            resume_from   = 0

            if os.path.exists(tmp_path):
                existing_size  = os.path.getsize(tmp_path)
                chunk_size     = hdr.get("chunk_size", 65536)
                resume_from    = existing_size // chunk_size

            # 2. Send TRANSFER_ACCEPT
            await self._send_frame(writer, {
                "version":     STP_VERSION,
                "msg_type":    "TRANSFER_ACCEPT",
                "resume_from": resume_from,
            })

            # 3. Receive chunks
            received = resume_from
            mode     = "ab" if resume_from > 0 else "wb"
            with open(tmp_path, mode) as out:
                while received < total_chunks:
                    c_hdr, payload = await self._recv_frame(reader)
                    msg_type = c_hdr.get("msg_type")

                    if msg_type == "TRANSFER_FAIL":
                        raise ConnectionAbortedError("Sender cancelled transfer")

                    if msg_type != "CHUNK_DATA":
                        continue

                    # Verify chunk integrity
                    expected_hash = c_hdr.get("chunk_hash", "")
                    actual_hash   = _chunk_sha256(payload)

                    if actual_hash != expected_hash:
                        await self._send_frame(writer, {
                            "version":  STP_VERSION,
                            "msg_type": "CHUNK_NACK",
                            "chunk_id": c_hdr.get("chunk_id"),
                        })
                        continue

                    out.write(payload)
                    received += 1

                    await self._send_frame(writer, {
                        "version":  STP_VERSION,
                        "msg_type": "CHUNK_ACK",
                        "chunk_id": c_hdr.get("chunk_id"),
                    })

                    if self.progress_cb:
                        self.progress_cb(received, total_chunks, music_id)

            # 4. Receive TRANSFER_END
            end_hdr, _ = await self._recv_frame(reader)
            if end_hdr.get("msg_type") != "TRANSFER_END":
                raise ValueError("Expected TRANSFER_END")

            # 5. Verify whole-file hash
            actual_file_hash = _file_sha256(tmp_path)
            if expected_file_hash and actual_file_hash != expected_file_hash:
                raise ValueError(f"File hash mismatch: {actual_file_hash} != {expected_file_hash}")

            # Rename .part → final
            os.replace(tmp_path, final_path)
            self.valid_tokens.discard(peer_token)

            if self.done_cb:
                self.done_cb(music_id, final_path)

        except Exception as exc:
            if self.error_cb:
                self.error_cb(music_id, str(exc))
            # Clean up partial file on hard failure (optional)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
