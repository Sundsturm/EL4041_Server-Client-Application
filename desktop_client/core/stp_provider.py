"""
core/stp_provider.py

Approval-flow STP provider side.

The owner/provider actively CONNECTS to the downloader after approving a
transfer request, then sends the file over STP.

Frame format:
    [4-byte FRAME_LEN][4-byte JSON_LEN][JSON HEADER][BINARY PAYLOAD]

Retry logic is built-in: the downloader (Termux/desktop) may not have
opened its listener port yet when the owner approves. We retry the TCP
connection up to MAX_CONNECT_ATTEMPTS times with exponential backoff.
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


def _build_frame(header: dict, payload: bytes = b"") -> bytes:
    json_bytes = json.dumps(header).encode("utf-8")
    json_len = len(json_bytes)
    frame_len = json_len + len(payload)
    return struct.pack(">II", frame_len, json_len) + json_bytes + payload


class STPProvider:
    """
    Active sender used by the owner after approving a transfer request.
    """

    def __init__(
        self,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
        error_cb: Optional[Callable[[str, str], None]] = None,
        chunk_kb: int = STP_DEFAULT_CHUNK_KB,
    ):
        self.progress_cb = progress_cb
        self.error_cb = error_cb
        self.chunk_size = chunk_kb * 1024
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    async def _send_frame(
        self,
        writer: asyncio.StreamWriter,
        header: dict,
        payload: bytes = b"",
    ):
        writer.write(_build_frame(header, payload))
        await writer.drain()

    async def _recv_frame(self, reader: asyncio.StreamReader) -> tuple[dict, bytes]:
        prefix = await reader.readexactly(8)
        frame_len, json_len = struct.unpack(">II", prefix)
        json_bytes = await reader.readexactly(json_len)
        payload = await reader.readexactly(frame_len - json_len)
        header = json.loads(json_bytes.decode("utf-8"))
        return header, payload

    async def send_one(
        self,
        peer_ip: str,
        peer_port: int,
        file_path: str,
        music_id: str,
        peer_token: str,
        mime_type: str = "",
        filename: str = "",
        request_id: str = "",
    ) -> str:
        """
        Connect to downloader and send one file.

        Returns the local file_path after successful transfer.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)

        file_size = os.path.getsize(file_path)
        total_chunks = math.ceil(file_size / self.chunk_size) if file_size else 0
        file_hash = _file_sha256(file_path)

        real_filename = filename or os.path.basename(file_path)
        ext = os.path.splitext(real_filename)[1].lower()
        real_mime = mime_type or SUPPORTED_MIME_TYPES.get(ext, "application/octet-stream")

        reader, writer = None, None
        MAX_CONNECT_ATTEMPTS = 5
        CONNECT_RETRY_BASE_S  = 2.0   # 2 s → 4 s → 8 s → 16 s → 32 s

        for attempt in range(1, MAX_CONNECT_ATTEMPTS + 1):
            try:
                reader, writer = await asyncio.open_connection(peer_ip, peer_port)
                break
            except (ConnectionRefusedError, OSError) as exc:
                if attempt < MAX_CONNECT_ATTEMPTS:
                    wait = CONNECT_RETRY_BASE_S * (2 ** (attempt - 1))
                    print(
                        f"[STP PROVIDER] connect attempt {attempt}/{MAX_CONNECT_ATTEMPTS} "
                        f"failed ({exc}). Retrying in {int(wait)}s…"
                    )
                    await asyncio.sleep(wait)
                else:
                    raise ConnectionRefusedError(
                        f"Could not connect to {peer_ip}:{peer_port} after "
                        f"{MAX_CONNECT_ATTEMPTS} attempts. "
                        "Make sure the downloader is listening. "
                        f"Last error: {exc}"
                    ) from exc

        try:
            await self._send_frame(writer, {
                "version": STP_VERSION,
                "msg_type": "TRANSFER_REQ",
                "request_id": request_id,
                "peer_token": peer_token,
                "music_id": music_id,
                "filename": real_filename,
                "mime_type": real_mime,
                "file_size": file_size,
                "file_hash": file_hash,
                "total_chunks": total_chunks,
                "chunk_size": self.chunk_size,
            })

            accept, _ = await self._recv_frame(reader)
            if accept.get("msg_type") == "TRANSFER_FAIL":
                raise ConnectionError(accept.get("reason", "transfer rejected"))
            if accept.get("msg_type") != "TRANSFER_ACCEPT":
                raise ConnectionError(f"expected TRANSFER_ACCEPT, got {accept}")

            start_chunk = int(accept.get("resume_from", 0))

            with open(file_path, "rb") as f:
                f.seek(start_chunk * self.chunk_size)

                for chunk_id in range(start_chunk, total_chunks):
                    if self._cancelled:
                        await self._send_frame(writer, {
                            "version": STP_VERSION,
                            "msg_type": "TRANSFER_FAIL",
                            "music_id": music_id,
                            "reason": "cancelled",
                        })
                        raise ConnectionAbortedError("upload cancelled")

                    data = f.read(self.chunk_size)
                    chunk_hash = _chunk_sha256(data)

                    await self._send_frame(writer, {
                        "version": STP_VERSION,
                        "msg_type": "CHUNK_DATA",
                        "music_id": music_id,
                        "chunk_id": chunk_id,
                        "total_chunks": total_chunks,
                        "chunk_size": len(data),
                        "chunk_hash": chunk_hash,
                        "is_last": chunk_id == total_chunks - 1,
                    }, payload=data)

                    ack, _ = await self._recv_frame(reader)

                    if ack.get("msg_type") == "CHUNK_NACK":
                        # Retry same chunk once.
                        await self._send_frame(writer, {
                            "version": STP_VERSION,
                            "msg_type": "CHUNK_DATA",
                            "music_id": music_id,
                            "chunk_id": chunk_id,
                            "total_chunks": total_chunks,
                            "chunk_size": len(data),
                            "chunk_hash": chunk_hash,
                            "is_last": chunk_id == total_chunks - 1,
                        }, payload=data)
                        ack, _ = await self._recv_frame(reader)

                    if ack.get("msg_type") != "CHUNK_ACK":
                        raise ConnectionError(f"expected CHUNK_ACK, got {ack}")

                    if self.progress_cb:
                        self.progress_cb(music_id, chunk_id + 1, total_chunks)

            await self._send_frame(writer, {
                "version": STP_VERSION,
                "msg_type": "TRANSFER_END",
                "music_id": music_id,
                "file_hash": file_hash,
            })

            return file_path

        except Exception as exc:
            if self.error_cb:
                self.error_cb(music_id, str(exc))
            raise

        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
