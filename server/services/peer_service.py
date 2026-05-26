"""
server/services/peer_service.py
---------------------------------
Manages the peer registry: online/offline status, Tailscale IPs,
peer discovery for a given song, and heartbeat updates.
"""

import uuid
from datetime import datetime, timezone

from server.database import get_db
from server.models.schemas import err, ok
from server.services import logging_service


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


async def register_peer(
    user_id: str,
    tailscale_ip: str,
    port: int,
) -> dict:
    """
    Register or update a peer's Tailscale address and mark it online.
    Uses UPSERT so re-publishing after a disconnect is safe.
    """
    # Derive a stable peer_id from user_id (one peer per user)
    peer_id = f"peer_{user_id[:8]}"
    now = _utcnow_iso()

    db = await get_db()
    await db.execute(
        """
        INSERT INTO peer_registry (peer_id, user_id, tailscale_ip, port, status, last_seen)
        VALUES (?, ?, ?, ?, 'online', ?)
        ON CONFLICT(peer_id) DO UPDATE SET
            tailscale_ip = excluded.tailscale_ip,
            port         = excluded.port,
            status       = 'online',
            last_seen    = excluded.last_seen
        """,
        (peer_id, user_id, tailscale_ip, port, now),
    )
    await db.commit()
    await logging_service.log_peer("REGISTER", user_id, peer_id, tailscale_ip, port)
    return ok({"peer_id": peer_id}, "Peer registered.")


async def unregister_peer(user_id: str) -> dict:
    """Mark the peer for *user_id* as offline."""
    peer_id = f"peer_{user_id[:8]}"
    db = await get_db()
    await db.execute(
        "UPDATE peer_registry SET status='offline' WHERE peer_id=?", (peer_id,)
    )
    await db.commit()
    await logging_service.log_peer("UNREGISTER", user_id, peer_id)
    return ok(message="Peer unregistered.")


async def get_peer_status(peer_id: str) -> dict:
    """Return the full peer_registry row for *peer_id*."""
    db = await get_db()
    async with db.execute(
        "SELECT peer_id, user_id, tailscale_ip, port, status, last_seen FROM peer_registry WHERE peer_id=?",
        (peer_id,),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        return err(f"Peer '{peer_id}' not found.")

    return ok(dict(row))


async def discover_peers(music_id: str) -> dict:
    """
    Find online peers that own a specific song.

    Returns a list of {peer_id, tailscale_ip, port} dicts.
    """
    db = await get_db()
    async with db.execute(
        """
        SELECT pr.peer_id, pr.tailscale_ip, pr.port
        FROM peer_registry pr
        JOIN music_metadata mm ON mm.owner_id = pr.user_id
        WHERE mm.music_id = ? AND pr.status = 'online'
        """,
        (music_id,),
    ) as cur:
        rows = await cur.fetchall()

    peers = [dict(r) for r in rows]
    if not peers:
        return err(f"No online peers found for music_id '{music_id}'.")

    return ok({"peers": peers})


async def heartbeat(user_id: str) -> dict:
    """Update last_seen for *user_id*'s peer entry."""
    peer_id = f"peer_{user_id[:8]}"
    now = _utcnow_iso()
    db = await get_db()
    await db.execute(
        "UPDATE peer_registry SET last_seen=?, status='online' WHERE peer_id=?",
        (now, peer_id),
    )
    await db.commit()
    await logging_service.log_peer("HEARTBEAT", user_id, peer_id)
    return ok({"last_seen": now})


async def list_online_peers() -> dict:
    """Return all currently online peers in the registry."""
    db = await get_db()
    async with db.execute(
        """
        SELECT pr.peer_id, pr.user_id, pr.tailscale_ip, pr.port,
               pr.status, pr.last_seen, u.username
        FROM peer_registry pr
        JOIN users u ON u.user_id = pr.user_id
        WHERE pr.status = 'online'
        ORDER BY pr.last_seen DESC
        """,
    ) as cur:
        rows = await cur.fetchall()
    return ok({"peers": [dict(r) for r in rows]})
