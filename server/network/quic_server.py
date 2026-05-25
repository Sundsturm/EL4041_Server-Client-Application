"""
server/network/quic_server.py
--------------------------------
QUIC/CSP server for Android/Termux clients.

Protocol (CSP over QUIC):
    Each bidirectional QUIC stream carries exactly one request/response pair.
    Wire format: [4-byte uint32 BE message length][UTF-8 JSON payload]

Authentication:
    Authenticated msg_types must include either:
      - "access_token" (JWT) in the JSON payload, or
      - "session_token" in the JSON payload (for REFRESH_REQ / HEARTBEAT).
    The handler resolves user_id before calling message_router.dispatch().

MTU:
    QUIC_MAX_DATAGRAM_SIZE is set to 1200 bytes to stay well within
    Tailscale/WireGuard's reduced MTU (~1280-1420 bytes).
"""

from __future__ import annotations

import json
import struct
from typing import Any

from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.asyncio.protocol import QuicStreamHandler
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent, StreamDataReceived

from server import config
from server.database import get_db
from server.models.schemas import err
from server.router.message_router import UNAUTHENTICATED_ROUTES, dispatch
from server.security import jwt_handler
from server.security.token_store import verify_session_token

# msg_types that accept a session_token instead of an access_token
SESSION_TOKEN_ROUTES = {"REFRESH_REQ", "HEARTBEAT", "LOGOUT_REQ"}


# ---------------------------------------------------------------------------
# Wire-format helpers
# ---------------------------------------------------------------------------

def _encode_message(data: dict) -> bytes:
    """Encode a dict as length-prefixed UTF-8 JSON."""
    body = json.dumps(data).encode("utf-8")
    header = struct.pack(">I", len(body))   # 4-byte big-endian uint32
    return header + body


def _decode_message(raw: bytes) -> dict | None:
    """Decode length-prefixed JSON. Returns None on parse error."""
    if len(raw) < 4:
        return None
    (length,) = struct.unpack(">I", raw[:4])
    if len(raw) < 4 + length:
        return None
    try:
        return json.loads(raw[4:4 + length].decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# QUIC Protocol handler
# ---------------------------------------------------------------------------

class CSPServerProtocol(QuicConnectionProtocol):
    """
    One instance per QUIC connection (one Android client).
    Each QUIC stream is an independent request/response pair.
    Incoming stream data is buffered per-stream until a complete message arrives.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._stream_buffers: dict[int, bytes] = {}

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, StreamDataReceived):
            stream_id = event.stream_id
            # Accumulate bytes for this stream
            self._stream_buffers[stream_id] = (
                self._stream_buffers.get(stream_id, b"") + event.data
            )
            if event.end_stream:
                raw = self._stream_buffers.pop(stream_id, b"")
                self._quic._loop.create_task(
                    self._handle_stream(stream_id, raw)
                )

    async def _handle_stream(self, stream_id: int, raw: bytes) -> None:
        """Parse one CSP message and send back the response on the same stream."""
        msg = _decode_message(raw)
        if msg is None:
            response = err("Malformed CSP message.")
            self._send_response(stream_id, response)
            return

        msg_type = msg.get("msg_type", "")
        payload = {k: v for k, v in msg.items() if k != "msg_type"}

        # Resolve client Tailscale IP
        tailscale_ip: str | None = None
        remote = self._quic._network_paths[0].addr if self._quic._network_paths else None
        if remote:
            ip = remote[0]
            if ip.startswith(config.TAILSCALE_IP_PREFIX):
                tailscale_ip = ip

        # Resolve user_id
        user_id: str | None = None

        if msg_type not in UNAUTHENTICATED_ROUTES:
            if msg_type in SESSION_TOKEN_ROUTES:
                session_token = payload.get("session_token", "")
                info = await verify_session_token(session_token)
                if info:
                    user_id = info.user_id
            else:
                access_token = payload.get("access_token", "")
                user_id = await self._resolve_jwt(access_token)

            if user_id is None:
                self._send_response(stream_id, err("Authentication required."))
                return

        response = await dispatch(
            msg_type,
            payload,
            user_id=user_id,
            tailscale_ip=tailscale_ip,
        )
        self._send_response(stream_id, response)

    async def _resolve_jwt(self, token: str) -> str | None:
        """
        Validate a JWT access token and return user_id, or None on failure.
        Looks up the per-session jwt_secret from the DB.
        """
        if not token:
            return None
        try:
            import jwt as pyjwt
            unverified = pyjwt.decode(token, options={"verify_signature": False})
            user_id = unverified.get("sub")
        except Exception:
            return None

        if not user_id:
            return None

        db = await get_db()
        async with db.execute(
            """
            SELECT jwt_secret FROM sessions
            WHERE user_id=? AND revoked=0 AND jwt_secret IS NOT NULL
            ORDER BY expires_at DESC LIMIT 1
            """,
            (user_id,),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            return None

        payload = jwt_handler.verify_access_token(token, row["jwt_secret"])
        return payload["sub"] if payload else None

    def _send_response(self, stream_id: int, data: dict) -> None:
        """Send a length-prefixed JSON response on the given stream and close it."""
        encoded = _encode_message(data)
        self._quic.send_stream_data(stream_id, encoded, end_stream=True)
        self.transmit()


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def build_quic_configuration() -> QuicConfiguration:
    """Build a server-side QUIC configuration (TLS 1.3 with Tailscale cert)."""
    cfg = QuicConfiguration(is_client=False)
    cfg.load_cert_chain(config.CERT_PATH, config.KEY_PATH)
    cfg.max_datagram_size = config.QUIC_MAX_DATAGRAM_SIZE
    return cfg


async def run_quic_server() -> None:
    """
    Start the QUIC/UDP server.  This coroutine runs indefinitely until cancelled.
    Called as an asyncio task from main.py.

    Note: newer aioquic versions return a QuicServer from serve() that does NOT
    support the async context manager protocol.  We therefore call serve() with
    await and keep the server alive by waiting on a never-set asyncio.Event.
    """
    import asyncio

    configuration = build_quic_configuration()
    server = await serve(
        host=config.QUIC_HOST,
        port=config.QUIC_PORT,
        configuration=configuration,
        create_protocol=CSPServerProtocol,
    )
    print(
        f"[QUIC] CSP server listening on "
        f"udp://{config.QUIC_HOST}:{config.QUIC_PORT}"
    )
    # Block until this task is cancelled (e.g. on server shutdown)
    try:
        await asyncio.Event().wait()
    finally:
        server.close()
