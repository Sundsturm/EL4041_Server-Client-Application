"""
core/stp_sender.py
STP (Song Transfer Protocol) — Sender side.

Frame layout per chunk:
┌─────────────────────┐
│  4 bytes  FRAME_LEN  │  (uint32 big-endian) = len(json_bytes) + len(payload)
│  4 bytes  JSON_LEN   │  (uint32 big-endian) = len(json_bytes)
│  N bytes  JSON HDR   │  UTF-8 JSON
│  M bytes  BINARY     │  raw file bytes
└─────────────────────┘
"""

import asyncio
import hashlib
import json
import math
import os
import struct
from typing import Callable, Optional

from config import STP_DEFAULT_CHUNK_KB, STP_VERSION, SUPPORTED_MIME_TYPES


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _chunk_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_frame(header: dict, payload: bytes) -> bytes:
    json_bytes = json.dumps(header).encode("utf-8")
    json_len   = len(json_bytes)
    frame_len  = json_len + len(payload)
    prefix     = struct.pack(">II", frame_len, json_len)
    return prefix + json_bytes + payload


class STPSender:
    """
    Connects to a peer and streams a song file using STP.

    Usage
    ─────
    sender = STPSender(
        peer_ip="192.168.1.5",
        peer_port=55000,
        peer_token="TRX_991A",
        file_path="/path/to/song.mp3",
        progress_cb=lambda sent, total: ...,
    )
    await sender.send()
    """

    def __init__(
        self,
        peer_ip: str,
        peer_port: int,
        peer_token: str,
        file_path: str,
        music_id: str,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        chunk_kb: int = STP_DEFAULT_CHUNK_KB,
    ):
        self.peer_ip    = peer_ip
        self.peer_port  = peer_port
        self.peer_token = peer_token
        self.file_path  = file_path
        self.music_id   = music_id
        self.progress_cb = progress_cb
        self.chunk_size  = chunk_kb * 1024

        ext       = os.path.splitext(file_path)[1].lower()
        self.mime = SUPPORTED_MIME_TYPES.get(ext, "application/octet-stream")
        self.filename = os.path.basename(file_path)

        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    # ─── Internal frame send ─────────────────────────────────────────────────

    async def _send_frame(self, writer: asyncio.StreamWriter, header: dict, payload: bytes = b""):
        frame = _build_frame(header, payload)
        writer.write(frame)
        await writer.drain()

    async def _recv_frame(self, reader: asyncio.StreamReader) -> tuple[dict, bytes]:
        prefix = await reader.readexactly(8)
        frame_len, json_len = struct.unpack(">II", prefix)
        json_bytes = await reader.readexactly(json_len)
        payload    = await reader.readexactly(frame_len - json_len)
        header     = json.loads(json_bytes.decode("utf-8"))
        return header, payload

    # ─── Main send flow ──────────────────────────────────────────────────────

    async def send(self):
        file_size   = os.path.getsize(self.file_path)
        total_chunks = math.ceil(file_size / self.chunk_size)
        file_hash   = _file_sha256(self.file_path)

        reader, writer = await asyncio.open_connection(self.peer_ip, self.peer_port)

        try:
            # 1. TRANSFER_REQ
            await self._send_frame(writer, {
                "version":      STP_VERSION,
                "msg_type":     "TRANSFER_REQ",
                "music_id":     self.music_id,
                "filename":     self.filename,
                "mime_type":    self.mime,
                "total_chunks": total_chunks,
                "chunk_size":   self.chunk_size,
                "file_hash":    file_hash,
                "peer_token":   self.peer_token,
            })

            # 2. Wait for TRANSFER_ACCEPT
            hdr, _ = await self._recv_frame(reader)
            if hdr.get("msg_type") != "TRANSFER_ACCEPT":
                raise ConnectionError(f"Transfer rejected: {hdr}")

            start_chunk = hdr.get("resume_from", 0)

            # 3. Send chunks
            with open(self.file_path, "rb") as f:
                f.seek(start_chunk * self.chunk_size)
                for chunk_id in range(start_chunk, total_chunks):
                    if self._cancelled:
                        await self._send_frame(writer, {
                            "version":  STP_VERSION,
                            "msg_type": "TRANSFER_FAIL",
                            "reason":   "cancelled",
                        })
                        return

                    data       = f.read(self.chunk_size)
                    chunk_hash = _chunk_sha256(data)

                    await self._send_frame(writer, {
                        "version":     STP_VERSION,
                        "msg_type":    "CHUNK_DATA",
                        "music_id":    self.music_id,
                        "chunk_id":    chunk_id,
                        "total_chunks":total_chunks,
                        "chunk_size":  len(data),
                        "chunk_hash":  chunk_hash,
                    }, payload=data)

                    # Wait for ACK
                    ack_hdr, _ = await self._recv_frame(reader)
                    if ack_hdr.get("msg_type") == "CHUNK_NACK":
                        # Retry once
                        f.seek(chunk_id * self.chunk_size)
                        data       = f.read(self.chunk_size)
                        chunk_hash = _chunk_sha256(data)
                        await self._send_frame(writer, {
                            "version":     STP_VERSION,
                            "msg_type":    "CHUNK_DATA",
                            "music_id":    self.music_id,
                            "chunk_id":    chunk_id,
                            "total_chunks":total_chunks,
                            "chunk_size":  len(data),
                            "chunk_hash":  chunk_hash,
                        }, payload=data)
                        ack_hdr, _ = await self._recv_frame(reader)

                    if self.progress_cb:
                        self.progress_cb(chunk_id + 1, total_chunks)

            # 4. TRANSFER_END
            await self._send_frame(writer, {
                "version":   STP_VERSION,
                "msg_type":  "TRANSFER_END",
                "music_id":  self.music_id,
                "file_hash": file_hash,
            })

        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
