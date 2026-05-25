"""
server/services/negotiation_service.py
----------------------------------------
Transfer negotiation: the server acts as a matchmaker.
It looks up an online provider, generates a short-lived HMAC-SHA256
signed peer token, and returns the provider's Tailscale address.

The sender peer then calls /peer/verify-token (REST) or SESSION_VERIFY
(CSP) before accepting the incoming STP connection -- this is the
"sender-calls-verify" approach agreed in the design.
"""

import uuid
from datetime import datetime, timezone

from server.database import get_db
from server.models.schemas import err, ok
from server.security.token_store import create_peer_token, verify_peer_token
from server.services import logging_service


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


async def request_download(requester_id: str, music_id: str) -> dict:
    """
    Handle a download request from *requester_id* for *music_id*.

    Steps
    -----
    1. Look up an online provider that owns the song.
    2. Fetch the provider's Tailscale IP + STP port from peer_registry.
    3. Generate a signed peer token.
    4. Record the negotiation.
    5. Return {peer_ip, peer_port, peer_token} to the requester.
    """
    db = await get_db()

    # Step 1-2: find an online provider
    async with db.execute(
        """
        SELECT pr.peer_id, pr.user_id, pr.tailscale_ip, pr.port
        FROM peer_registry pr
        JOIN music_metadata mm ON mm.owner_id = pr.user_id
        WHERE mm.music_id = ? AND pr.status = 'online'
        ORDER BY pr.last_seen DESC
        LIMIT 1
        """,
        (music_id,),
    ) as cur:
        provider = await cur.fetchone()

    if provider is None:
        return err("No online peer is currently sharing this song.")

    provider_id   = provider["user_id"]
    peer_ip       = provider["tailscale_ip"]
    peer_port     = provider["port"]

    # Step 3: generate peer token
    peer_token = await create_peer_token(music_id, requester_id, provider_id)

    # Step 4: record negotiation
    negotiation_id = str(uuid.uuid4())
    now = _utcnow_iso()
    await db.execute(
        """
        INSERT INTO transfer_negotiation
            (negotiation_id, peer_token, peer_ip, peer_port, status)
        VALUES (?, ?, ?, ?, 'pending')
        """,
        (negotiation_id, peer_token, peer_ip, peer_port),
    )
    await db.execute(
        """
        INSERT INTO download_history
            (requester_id, music_id, peer_id, timestamp, status)
        VALUES (?, ?, ?, ?, 'negotiated')
        """,
        (requester_id, music_id, provider["peer_id"], now),
    )
    await db.commit()

    await logging_service.log(
        "INFO",
        "negotiation",
        f"Transfer negotiated: music={music_id} requester={requester_id} provider={provider_id}",
    )

    return ok(
        {
            "peer_ip": peer_ip,
            "peer_port": peer_port,
            "peer_token": peer_token,
        },
        "Transfer negotiation successful.",
    )


async def verify_transfer_token(peer_token: str) -> dict:
    """
    Called by the **sender peer** before accepting an incoming STP connection.
    Validates the HMAC, checks expiry, and marks the token as used (one-time).

    Returns provider/requester info so the sender can confirm the identity
    of who is about to connect.
    """
    info = await verify_peer_token(peer_token)
    if info is None:
        return err("Peer token is invalid, expired, or already used.")

    return ok(
        {
            "music_id":      info.music_id,
            "requester_id":  info.requester_id,
            "provider_id":   info.provider_id,
        },
        "Peer token verified.",
    )
