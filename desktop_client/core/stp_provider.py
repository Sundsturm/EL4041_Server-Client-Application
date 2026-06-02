"""
core/stp_provider.py
STP provider side for P2P download.

This component runs on the peer that owns/published a song. It listens on a TCP
port, receives a TRANSFER_REQ from a downloader, verifies the server-issued
peer_token, looks up the local file by music_id, then streams the file chunks.

Frame format matches the existing desktop STP code:
    [4-byte FRAME_LEN][4-byte JSON_LEN][JSON HEADER][BINARY PAYLOAD]
"""

import asyncio
import hashlib
import json
import math
import os
import struct
from typing import Callable, Optional

from config import STP_DEFAULT_CHUNK_KB, STP_LISTEN_PORT, STP_VERSION, SUPPORTED_MIME_TYPES
from core.api_client import APIClient


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
    TCP file provider for published songs.

    The provider keeps a mapping of music_id -> local file path. When a peer
    requests a music_id with a valid peer_token, this provider sends the file.
    """

    def __init__(
        self,
        api: APIClient,
        shared_files: dict[str, str],
        listen_port: int = STP_LISTEN_PORT,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
        error_cb: Optional[Callable[[str, str], None]] = None,
        chunk_kb: int = STP_DEFAULT_CHUNK_KB,
    ):
        self._api = api
        self._shared_files = shared_files
        self.listen_port = listen_port
        self.progress_cb = progress_cb
        self.error_cb = error_cb
        self.chunk_size = chunk_kb * 1024
        self._server: Optional[asyncio.Server] = None

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_connection,
            host="0.0.0.0",
            port=self.listen_port,
        )
        asyncio.ensure_future(self._server.serve_forever())
        print(f"[STP PROVIDER] listening on 0.0.0.0:{self.listen_port}")

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def register_file(self, music_id: str, local_path: str):
        if music_id and local_path:
            self._shared_files[music_id] = local_path
            print(f"[STP PROVIDER] shared {music_id} -> {local_path}")

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

    async def _send_fail(
        self,
        writer: asyncio.StreamWriter,
        reason: str,
        music_id: str = "",
    ):
        await self._send_frame(writer, {
            "version": STP_VERSION,
            "msg_type": "TRANSFER_FAIL",
            "music_id": music_id,
            "reason": reason,
        })

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        music_id = "unknown"
        try:
            req, _ = await self._recv_frame(reader)

            if req.get("msg_type") != "TRANSFER_REQ":
                await self._send_fail(writer, "expected TRANSFER_REQ")
                return

            peer_token = req.get("peer_token", "")
            music_id = req.get("music_id", "")

            if not peer_token or not music_id:
                await self._send_fail(writer, "missing peer_token or music_id", music_id)
                return

            # Verify token with server before sending any file bytes.
            verify = await self._api.verify_peer_token(peer_token)
            verified_music_id = verify.get("music_id")
            if verified_music_id and verified_music_id != music_id:
                await self._send_fail(writer, "peer_token music_id mismatch", music_id)
                return

            file_path = self._shared_files.get(music_id)
            if not file_path or not os.path.exists(file_path):
                await self._send_fail(writer, "file is not available on this peer", music_id)
                return

            file_size = os.path.getsize(file_path)
            total_chunks = math.ceil(file_size / self.chunk_size) if file_size else 0
            file_hash = _file_sha256(file_path)
            filename = os.path.basename(file_path)
            ext = os.path.splitext(file_path)[1].lower()
            mime = SUPPORTED_MIME_TYPES.get(ext, "application/octet-stream")

            await self._send_frame(writer, {
                "version": STP_VERSION,
                "msg_type": "TRANSFER_ACCEPT",
                "peer_token": peer_token,
                "music_id": music_id,
                "filename": filename,
                "mime_type": mime,
                "file_size": file_size,
                "file_hash": file_hash,
                "total_chunks": total_chunks,
                "chunk_size": self.chunk_size,
                "status": "OK",
            })

            with open(file_path, "rb") as f:
                for chunk_id in range(total_chunks):
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
                        # retry the same chunk once
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

        except Exception as exc:
            print(f"[STP PROVIDER ERROR] {music_id}: {exc}")
            if self.error_cb:
                self.error_cb(music_id, str(exc))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
