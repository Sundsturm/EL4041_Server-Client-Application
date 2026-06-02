"""
core/stp_downloader.py
STP downloader side for P2P download.

The downloader connects to the provider peer returned by /download, sends a
TRANSFER_REQ, receives file chunks, verifies SHA256 integrity, and writes the
song to the local music directory.

Frame format matches the existing desktop STP code:
    [4-byte FRAME_LEN][4-byte JSON_LEN][JSON HEADER][BINARY PAYLOAD]
"""

import asyncio
import hashlib
import json
import os
import struct
from typing import Callable, Optional

from config import MUSIC_DIR, STP_VERSION


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
    json_len = len(json_bytes)
    frame_len = json_len + len(payload)
    return struct.pack(">II", frame_len, json_len) + json_bytes + payload


def _safe_download_path(filename: str) -> str:
    os.makedirs(MUSIC_DIR, exist_ok=True)
    base = os.path.basename(filename) or "downloaded_song.bin"
    candidate = os.path.join(MUSIC_DIR, base)

    if not os.path.exists(candidate):
        return candidate

    name, ext = os.path.splitext(base)
    i = 1
    while True:
        candidate = os.path.join(MUSIC_DIR, f"{name}_{i}{ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


class STPDownloader:
    def __init__(
        self,
        peer_ip: str,
        peer_port: int,
        peer_token: str,
        music_id: str,
        filename: str = "",
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
        done_cb: Optional[Callable[[str, str], None]] = None,
        error_cb: Optional[Callable[[str, str], None]] = None,
    ):
        self.peer_ip = peer_ip
        self.peer_port = peer_port
        self.peer_token = peer_token
        self.music_id = music_id
        self.filename = filename or f"{music_id}.bin"
        self.progress_cb = progress_cb
        self.done_cb = done_cb
        self.error_cb = error_cb
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    async def _recv_frame(self, reader: asyncio.StreamReader) -> tuple[dict, bytes]:
        prefix = await reader.readexactly(8)
        frame_len, json_len = struct.unpack(">II", prefix)
        json_bytes = await reader.readexactly(json_len)
        payload = await reader.readexactly(frame_len - json_len)
        header = json.loads(json_bytes.decode("utf-8"))
        return header, payload

    async def _send_frame(
        self,
        writer: asyncio.StreamWriter,
        header: dict,
        payload: bytes = b"",
    ):
        writer.write(_build_frame(header, payload))
        await writer.drain()

    async def download(self):
        final_path = ""
        tmp_path = ""
        try:
            reader, writer = await asyncio.open_connection(self.peer_ip, self.peer_port)

            try:
                await self._send_frame(writer, {
                    "version": STP_VERSION,
                    "msg_type": "TRANSFER_REQ",
                    "peer_token": self.peer_token,
                    "music_id": self.music_id,
                    "filename": self.filename,
                })

                accept, _ = await self._recv_frame(reader)
                if accept.get("msg_type") == "TRANSFER_FAIL":
                    raise ConnectionError(accept.get("reason", "transfer rejected"))
                if accept.get("msg_type") != "TRANSFER_ACCEPT":
                    raise ConnectionError(f"expected TRANSFER_ACCEPT, got {accept}")

                filename = accept.get("filename") or self.filename
                total_chunks = int(accept.get("total_chunks", 0))
                expected_file_hash = accept.get("file_hash", "")

                final_path = _safe_download_path(filename)
                tmp_path = final_path + ".part"

                received = 0
                with open(tmp_path, "wb") as out:
                    while received < total_chunks:
                        if self._cancelled:
                            await self._send_frame(writer, {
                                "version": STP_VERSION,
                                "msg_type": "TRANSFER_FAIL",
                                "music_id": self.music_id,
                                "reason": "cancelled",
                            })
                            raise ConnectionAbortedError("download cancelled")

                        hdr, payload = await self._recv_frame(reader)
                        msg_type = hdr.get("msg_type")

                        if msg_type == "TRANSFER_FAIL":
                            raise ConnectionError(hdr.get("reason", "provider failed"))

                        if msg_type != "CHUNK_DATA":
                            continue

                        expected_hash = hdr.get("chunk_hash", "")
                        actual_hash = _chunk_sha256(payload)

                        if expected_hash and actual_hash != expected_hash:
                            await self._send_frame(writer, {
                                "version": STP_VERSION,
                                "msg_type": "CHUNK_NACK",
                                "music_id": self.music_id,
                                "chunk_id": hdr.get("chunk_id", received),
                                "reason": "chunk_hash mismatch",
                            })
                            continue

                        out.write(payload)
                        received += 1

                        await self._send_frame(writer, {
                            "version": STP_VERSION,
                            "msg_type": "CHUNK_ACK",
                            "music_id": self.music_id,
                            "chunk_id": hdr.get("chunk_id", received - 1),
                        })

                        if self.progress_cb:
                            self.progress_cb(received, total_chunks, self.music_id)

                end_hdr, _ = await self._recv_frame(reader)
                if end_hdr.get("msg_type") != "TRANSFER_END":
                    raise ValueError(f"expected TRANSFER_END, got {end_hdr}")

                if expected_file_hash:
                    actual_file_hash = _file_sha256(tmp_path)
                    if actual_file_hash != expected_file_hash:
                        raise ValueError(
                            f"file hash mismatch: {actual_file_hash} != {expected_file_hash}"
                        )

                os.replace(tmp_path, final_path)

                if self.done_cb:
                    self.done_cb(self.music_id, final_path)

                return final_path

            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        except Exception as exc:
            if self.error_cb:
                self.error_cb(self.music_id, str(exc))
            if tmp_path and os.path.exists(tmp_path):
                # Keep partial file only if you want resume. For now remove it to avoid confusion.
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            raise
