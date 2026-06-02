"""
core/stp_downloader.py

Approval-flow STP downloader side.

In this version the downloader LISTENS on STP_LISTEN_PORT after submitting a
download request. When the owner approves the request, the owner connects to
this listener and sends the file chunks.

Frame format:
    [4-byte FRAME_LEN][4-byte JSON_LEN][JSON HEADER][BINARY PAYLOAD]
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
    """
    One-shot STP receiver for a single download request.

    It binds to listen_port, accepts one provider connection, receives the file,
    then closes the server.
    """

    def __init__(
        self,
        listen_port: int = STP_LISTEN_PORT,
        music_id: str = "",
        filename: str = "",
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
        done_cb: Optional[Callable[[str, str], None]] = None,
        error_cb: Optional[Callable[[str, str], None]] = None,
        accept_timeout: float = 180.0,
    ):
        self.listen_port = listen_port
        self.music_id = music_id
        self.filename = filename or f"{music_id}.bin"
        self.progress_cb = progress_cb
        self.done_cb = done_cb
        self.error_cb = error_cb
        self.accept_timeout = accept_timeout

        self._cancelled = False
        self._server: Optional[asyncio.Server] = None
        self._done_future: Optional[asyncio.Future] = None

    def cancel(self):
        self._cancelled = True
        if self._done_future and not self._done_future.done():
            self._done_future.set_exception(ConnectionAbortedError("download cancelled"))

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
            "music_id": music_id or self.music_id,
            "reason": reason,
        })

    async def listen_and_receive(self) -> str:
        """
        Listen for exactly one provider connection and receive the file.

        Returns
        -------
        str
            Final downloaded file path.
        """
        loop = asyncio.get_running_loop()
        self._done_future = loop.create_future()

        self._server = await asyncio.start_server(
            self._handle_connection,
            host="0.0.0.0",
            port=self.listen_port,
        )

        print(f"[STP DOWNLOADER] waiting on 0.0.0.0:{self.listen_port}")

        try:
            return await asyncio.wait_for(self._done_future, timeout=self.accept_timeout)
        finally:
            if self._server:
                self._server.close()
                await self._server.wait_closed()
                self._server = None
            print("[STP DOWNLOADER] listener closed")

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        final_path = ""
        tmp_path = ""
        music_id = self.music_id or "unknown"

        try:
            req, _ = await self._recv_frame(reader)
            if req.get("msg_type") != "TRANSFER_REQ":
                await self._send_fail(writer, "expected TRANSFER_REQ", music_id)
                return

            req_music_id = req.get("music_id", "")
            if self.music_id and req_music_id and req_music_id != self.music_id:
                await self._send_fail(writer, "music_id mismatch", req_music_id)
                return

            music_id = req_music_id or self.music_id
            filename = req.get("filename") or self.filename
            total_chunks = int(req.get("total_chunks", 0))
            expected_file_hash = req.get("file_hash", "")
            chunk_size = int(req.get("chunk_size", 0))

            if total_chunks <= 0:
                await self._send_fail(writer, "invalid total_chunks", music_id)
                return

            final_path = _safe_download_path(filename)
            tmp_path = final_path + ".part"

            await self._send_frame(writer, {
                "version": STP_VERSION,
                "msg_type": "TRANSFER_ACCEPT",
                "music_id": music_id,
                "filename": os.path.basename(final_path),
                "resume_from": 0,
                "chunk_size": chunk_size,
                "status": "OK",
            })

            received = 0
            with open(tmp_path, "wb") as out:
                while received < total_chunks:
                    if self._cancelled:
                        await self._send_fail(writer, "download cancelled", music_id)
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
                            "music_id": music_id,
                            "chunk_id": hdr.get("chunk_id", received),
                            "reason": "chunk_hash mismatch",
                        })
                        continue

                    out.write(payload)
                    received += 1

                    await self._send_frame(writer, {
                        "version": STP_VERSION,
                        "msg_type": "CHUNK_ACK",
                        "music_id": music_id,
                        "chunk_id": hdr.get("chunk_id", received - 1),
                    })

                    if self.progress_cb:
                        self.progress_cb(received, total_chunks, music_id)

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
                self.done_cb(music_id, final_path)

            if self._done_future and not self._done_future.done():
                self._done_future.set_result(final_path)

        except Exception as exc:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

            if self.error_cb:
                self.error_cb(music_id, str(exc))

            if self._done_future and not self._done_future.done():
                self._done_future.set_exception(exc)

        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
