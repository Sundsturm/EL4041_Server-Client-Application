"""
core/csp_client.py
CSP control-plane client over QUIC using aioquic.

This version matches the server implementation:
- Each bidirectional QUIC stream carries exactly one request/response pair.
- Wire format: [4-byte uint32 big-endian length][UTF-8 JSON payload]
- JSON payload is FLAT, for example:
  {"msg_type":"LOGIN_REQ", "username":"alice", "password":"secret"}
- Authenticated requests include "access_token" at top level.
- REFRESH_REQ, LOGOUT_REQ, and HEARTBEAT use "session_token".
"""

from __future__ import annotations

import asyncio
import json
import ssl
import struct
from typing import Any

# Default timeout (seconds) for a CSP round-trip when server is reachable.
CSP_REQUEST_TIMEOUT: float = 8.0
# Shorter timeout used for logout / exit so the client doesn't hang.
CSP_LOGOUT_TIMEOUT: float = 5.0

try:
    from aioquic.asyncio.client import connect as _quic_connect
    from aioquic.quic.configuration import QuicConfiguration as _QuicConfiguration
    _AIOQUIC_AVAILABLE = True
except ImportError:
    _AIOQUIC_AVAILABLE = False

from config import SERVER_HOST, SERVER_QUIC_PORT
from core.auth_manager import AuthManager


class CSPError(Exception):
    pass


SESSION_TOKEN_ROUTES = {"REFRESH_REQ", "HEARTBEAT", "LOGOUT_REQ"}
UNAUTHENTICATED_ROUTES = {"LOGIN_REQ", "REGISTER_REQ", "TIME_SYNC_REQ", "SESSION_VERIFY"}


class CSPClient:
    def __init__(self, auth: AuthManager, host: str = SERVER_HOST, port: int = SERVER_QUIC_PORT):
        self.auth = auth
        self.host = host
        self.port = port

    @staticmethod
    def _encode_message(data: dict[str, Any]) -> bytes:
        body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return struct.pack(">I", len(body)) + body

    @staticmethod
    def _decode_message(raw: bytes) -> dict[str, Any]:
        if len(raw) < 4:
            raise CSPError("Malformed CSP response: missing length prefix.")
        (length,) = struct.unpack(">I", raw[:4])
        body = raw[4:4 + length]
        if len(body) != length:
            raise CSPError("Malformed CSP response: incomplete JSON body.")
        try:
            return json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise CSPError(f"Invalid CSP JSON response: {exc}") from exc

    def _make_message(self, msg_type: str, payload: dict | None = None, auth: bool = True) -> dict:
        msg: dict[str, Any] = {"msg_type": msg_type}
        if payload:
            msg.update(payload)

        if not auth or msg_type in UNAUTHENTICATED_ROUTES:
            return msg

        if msg_type in SESSION_TOKEN_ROUTES:
            session = self.auth.get_session_token()
            if session:
                msg["session_token"] = session
        else:
            token = self.auth.get_access_token()
            if token:
                msg["access_token"] = token
        return msg

    async def send_request(
        self,
        msg_type: str,
        payload: dict | None = None,
        auth: bool = True,
        timeout: float = CSP_REQUEST_TIMEOUT,
    ) -> dict:
        if not _AIOQUIC_AVAILABLE:
            raise CSPError(
                "Module 'aioquic' tidak ditemukan.\n"
                "Install dengan: pip install aioquic"
            )

        configuration = _QuicConfiguration(is_client=True)
        # Development-friendly for self-signed/Tailscale certs.
        # For production, install the server certificate and enable verification.
        configuration.verify_mode = ssl.CERT_NONE

        message = self._make_message(msg_type, payload, auth=auth)
        encoded = self._encode_message(message)

        async def _do_quic() -> list[bytes]:
            async with _quic_connect(self.host, self.port, configuration=configuration) as client:
                reader, writer = await client.create_stream()
                writer.write(encoded)
                writer.write_eof()

                chunks: list[bytes] = []
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    chunks.append(data)
                return chunks

        try:
            chunks = await asyncio.wait_for(_do_quic(), timeout=timeout)
        except asyncio.TimeoutError:
            raise CSPError(
                f"Request '{msg_type}' timeout setelah {timeout:.0f}s "
                "— server tidak dapat dijangkau."
            )

        if not chunks:
            raise CSPError("No response from CSP server.")

        body = self._decode_message(b"".join(chunks))

        if body.get("status") == "error":
            raise CSPError(body.get("message") or str(body))

        if "data" in body:
            return body.get("data") or {}
        return body

    async def register(self, username: str, password: str) -> dict:
        return await self.send_request("REGISTER_REQ", {
            "username": username,
            "password": password,
        }, auth=False)

    async def login(self, username: str, password: str) -> dict:
        data = await self.send_request("LOGIN_REQ", {
            "username": username,
            "password": password,
        }, auth=False)
        self.auth.save_access_token(data["access_token"])
        self.auth.save_session_token(data["session_token"])
        self.auth.save_profile({
            "user_id": data.get("user_id"),
            "username": username,
        })
        return data

    async def logout(self) -> dict:
        # Gunakan timeout pendek agar exit/logout tidak menggantung saat server mati.
        data = await self.send_request("LOGOUT_REQ", {}, auth=True, timeout=CSP_LOGOUT_TIMEOUT)
        self.auth.logout_local()
        return data

    async def refresh(self) -> dict:
        data = await self.send_request("REFRESH_REQ", {}, auth=True)
        if data.get("access_token"):
            self.auth.save_access_token(data["access_token"])
        return data

    async def heartbeat(self) -> dict:
        return await self.send_request("HEARTBEAT", {}, auth=True)

    async def publish(self, metadata: dict) -> dict:
        return await self.send_request("PUBLISH_REQ", metadata, auth=True)

    async def search(self, query: str, limit: int = 50) -> dict:
        # Server router currently uses only q.
        return await self.send_request("SUBSCRIBE_REQ", {"q": query, "limit": limit}, auth=True)

    async def list_songs(self, limit: int = 100) -> dict:
        """LIST_SONGS_REQ — return all songs with full metadata (no filter)."""
        return await self.send_request("LIST_SONGS_REQ", {"limit": limit}, auth=True)

    async def download(self, music_id: str, requester_port: int = 5050) -> dict:
        return await self.send_request("DOWNLOAD_REQ", {
            "music_id": music_id,
            "requester_port": requester_port,
        }, auth=True)

    async def get_pending_requests(self) -> dict:
        """Poll server for pending download requests (owner side)."""
        return await self.send_request("PENDING_REQUESTS_REQ", {}, auth=True)

    async def approve_transfer(self, request_id: str) -> dict:
        return await self.send_request("APPROVE_TRANSFER_REQ", {
            "request_id": request_id,
        }, auth=True)

    async def reject_transfer(self, request_id: str, reason: str = "") -> dict:
        return await self.send_request("REJECT_TRANSFER_REQ", {
            "request_id": request_id,
            "reason": reason,
        }, auth=True)

    async def get_transfer_status(self, request_id: str) -> dict:
        return await self.send_request("TRANSFER_STATUS_REQ", {
            "request_id": request_id,
        }, auth=True)

    async def get_my_downloads(self) -> dict:
        return await self.send_request("MY_DOWNLOADS_REQ", {}, auth=True)

    async def update_transfer_status(self, request_id: str, status: str) -> dict:
        return await self.send_request("UPDATE_TRANSFER_STATUS_REQ", {
            "request_id": request_id,
            "status": status,
        }, auth=True)

    async def history(self, history_type: str = "download") -> dict:
        return await self.send_request("HISTORY_REQ", {"history_type": history_type}, auth=True)

    async def get_profile(self) -> dict:
        return await self.send_request("PROFILE_GET_REQ", {}, auth=True)

    async def update_profile(
        self,
        username: str = "",
        bio: str = "",
        password: str = "",
    ) -> dict:
        payload: dict = {}
        if username:
            payload["username"] = username
        if bio:
            payload["bio"] = bio
        if password:
            payload["password"] = password
        return await self.send_request("PROFILE_UPDATE_REQ", payload, auth=True)

    async def delete_profile(self, password: str) -> dict:
        data = await self.send_request("PROFILE_DELETE_REQ", {"password": password}, auth=True)
        self.auth.logout_local()
        return data

    async def time_sync(self) -> dict:
        return await self.send_request("TIME_SYNC_REQ", {}, auth=False)

    async def verify_peer_token(self, peer_token: str) -> dict:
        return await self.send_request("SESSION_VERIFY", {"peer_token": peer_token}, auth=False)
