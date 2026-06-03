"""
server/services/negotiation_service.py
----------------------------------------
Transfer negotiation with approval-based P2P flow.

Lifecycle
---------
1. Downloader calls request_download()  → status: 'pending'
2. Owner polls get_pending_requests()   → sees pending list
3. Owner calls approve_request()        → status: 'approved', peer_token issued,
                                          returns requester IP+port to owner
4. Owner connects via STP, transfer starts
5. update_transfer_status() marks 'in_progress' → 'completed'/'failed'
   (called by the client after STP result)

Alternatively at step 3, owner calls reject_request() → status: 'rejected'
"""

import asyncio
import uuid
from datetime import datetime, timezone

from server.database import get_db
from server.models.schemas import err, ok
from server.security.token_store import create_peer_token, verify_peer_token
from server.services import logging_service


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Downloader side
# ---------------------------------------------------------------------------

async def request_download(
    requester_id: str,
    music_id: str,
    requester_ip: str,
    requester_port: int = 5050,
) -> dict:
    """
    Submit a download request. Stores a 'pending' record and returns
    the request_id to the downloader. The actual transfer only starts
    after the owner calls approve_request().
    """
    db = await get_db()

    # Verify the song exists and find its owner
    async with db.execute(
        """
        SELECT mm.owner_id, mm.filename, mm.title,
               pr.tailscale_ip AS provider_ip, pr.port AS provider_port,
               pr.status AS peer_status
        FROM music_metadata mm
        LEFT JOIN peer_registry pr ON pr.user_id = mm.owner_id
        WHERE mm.music_id = ?
        """,
        (music_id,),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        return err("Song not found.")

    provider_id = row["owner_id"]

    if provider_id == requester_id:
        return err("You cannot download your own song.")

    if row["peer_status"] != "online":
        return err("The owner of this song is currently offline.")

    # Cancel any previous pending requests from this requester for the same song.
    # This prevents the owner from seeing stale requests after a retry,
    # and ensures only the latest request_id is active.
    now = _utcnow_iso()
    await db.execute(
        """
        UPDATE download_requests
        SET status = 'superseded', updated_at = ?
        WHERE requester_id = ? AND music_id = ? AND status = 'pending'
        """,
        (now, requester_id, music_id),
    )

    request_id = str(uuid.uuid4())

    await db.execute(
        """
        INSERT INTO download_requests
            (request_id, music_id, requester_id, provider_id,
             requester_ip, requester_port, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (request_id, music_id, requester_id, provider_id,
         requester_ip, requester_port, now, now),
    )
    await db.commit()

    song_title = row["title"] or row["filename"]
    await logging_service.log(
        "INFO", "negotiation",
        f"Download request submitted: music={music_id} req={requester_id} prov={provider_id}",
    )

    return ok(
        {
            "request_id": request_id,
            "song_title": song_title,
            "status": "pending",
        },
        "Download request submitted. Waiting for owner approval.",
    )


async def get_transfer_status(requester_id: str, request_id: str) -> dict:
    """Return the current status of a specific download request."""
    db = await get_db()
    async with db.execute(
        """
        SELECT dr.request_id, dr.music_id, dr.status, dr.reject_reason,
               dr.created_at, dr.updated_at,
               mm.title, mm.filename, mm.artist
        FROM download_requests dr
        JOIN music_metadata mm ON mm.music_id = dr.music_id
        WHERE dr.request_id = ? AND dr.requester_id = ?
        """,
        (request_id, requester_id),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        return err("Request not found.")

    return ok(dict(row))


async def list_my_downloads(requester_id: str) -> dict:
    """
    Return download records for the requester with status
    'in_progress' or 'completed' only.
    """
    db = await get_db()
    async with db.execute(
        """
        SELECT dr.request_id, dr.music_id, dr.status,
               dr.created_at, dr.updated_at,
               mm.title, mm.filename, mm.artist, mm.album
        FROM download_requests dr
        JOIN music_metadata mm ON mm.music_id = dr.music_id
        WHERE dr.requester_id = ?
          AND dr.status IN ('in_progress', 'completed', 'failed')
        ORDER BY dr.updated_at DESC
        """,
        (requester_id,),
    ) as cur:
        rows = await cur.fetchall()

    return ok({"downloads": [dict(r) for r in rows]})


async def update_transfer_status(request_id: str, status: str) -> dict:
    """
    Update the status of a download request after STP transfer result.
    Called by the owner's client after send_file_to_peer() completes.
    Valid transitions: approved→in_progress, in_progress→completed/failed
    """
    valid = {"in_progress", "completed", "failed"}
    if status not in valid:
        return err(f"Invalid status '{status}'. Must be one of: {valid}")

    db = await get_db()
    now = _utcnow_iso()
    await db.execute(
        "UPDATE download_requests SET status=?, updated_at=? WHERE request_id=?",
        (status, now, request_id),
    )
    await db.commit()
    return ok({"request_id": request_id, "status": status})


# ---------------------------------------------------------------------------
# Owner side
# ---------------------------------------------------------------------------

async def get_pending_requests(
    provider_id: str,
    long_poll_timeout: float = 28.0,
) -> dict:
    """
    Return all pending download requests for songs owned by provider_id.

    Long-polling: if there are no pending requests, waits up to
    `long_poll_timeout` seconds (checking every 2 s) before returning
    an empty list. This avoids repeated short polls from the client.
    """
    db = await get_db()
    deadline = asyncio.get_event_loop().time() + long_poll_timeout

    async def _fetch():
        async with db.execute(
            """
            SELECT dr.request_id, dr.music_id, dr.requester_id,
                   dr.requester_ip, dr.requester_port,
                   dr.created_at,
                   mm.title, mm.filename, mm.artist,
                   u.username AS requester_name
            FROM download_requests dr
            JOIN music_metadata mm ON mm.music_id = dr.music_id
            JOIN users u ON u.user_id = dr.requester_id
            WHERE mm.owner_id = ? AND dr.status = 'pending'
            ORDER BY dr.created_at ASC
            """,
            (provider_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    while True:
        rows = await _fetch()
        if rows:
            return ok({"requests": rows})
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return ok({"requests": []}, "No pending requests.")
        await asyncio.sleep(min(2.0, remaining))


async def approve_request(provider_id: str, request_id: str) -> dict:
    """
    Owner approves a pending download request.

    Generates a peer_token, updates status to 'approved', and returns
    the requester's IP + port so the owner can initiate the STP transfer.
    """
    db = await get_db()

    async with db.execute(
        """
        SELECT dr.*, mm.owner_id, mm.music_id AS mm_music_id,
               mm.filename, mm.title, mm.size, mm.hmac_hash, mm.mime_type
        FROM download_requests dr
        JOIN music_metadata mm ON mm.music_id = dr.music_id
        WHERE dr.request_id = ? AND mm.owner_id = ? AND dr.status = 'pending'
        """,
        (request_id, provider_id),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        return err("Request not found or already processed.")

    music_id    = row["music_id"]
    requester_id = row["requester_id"]

    # Generate one-time peer token
    peer_token = await create_peer_token(music_id, requester_id, provider_id)
    now = _utcnow_iso()

    await db.execute(
        """
        UPDATE download_requests
        SET status='approved', peer_token=?, updated_at=?
        WHERE request_id=?
        """,
        (peer_token, now, request_id),
    )
    await db.commit()

    await logging_service.log(
        "INFO", "negotiation",
        f"Transfer approved: request={request_id} music={music_id} "
        f"provider={provider_id} requester={requester_id}",
    )

    return ok(
        {
            "request_id":     request_id,
            "music_id":       music_id,
            "filename":       row["filename"],
            "title":          row["title"],
            "size":           row["size"],
            "hmac_hash":      row["hmac_hash"],
            "mime_type":      row["mime_type"],
            "requester_ip":   row["requester_ip"],
            "requester_port": row["requester_port"],
            "peer_token":     peer_token,
        },
        "Request approved. Connect to requester via STP.",
    )


async def reject_request(
    provider_id: str,
    request_id: str,
    reason: str = "",
) -> dict:
    """
    Owner rejects a pending download request.
    Also called automatically if the owner cannot locate the file locally.
    """
    db = await get_db()

    async with db.execute(
        """
        SELECT dr.request_id
        FROM download_requests dr
        JOIN music_metadata mm ON mm.music_id = dr.music_id
        WHERE dr.request_id = ? AND mm.owner_id = ? AND dr.status = 'pending'
        """,
        (request_id, provider_id),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        return err("Request not found or already processed.")

    now = _utcnow_iso()
    reject_reason = reason or "Rejected by owner."
    await db.execute(
        """
        UPDATE download_requests
        SET status='rejected', reject_reason=?, updated_at=?
        WHERE request_id=?
        """,
        (reject_reason, now, request_id),
    )
    await db.commit()

    await logging_service.log(
        "INFO", "negotiation",
        f"Transfer rejected: request={request_id} reason={reject_reason}",
    )

    return ok({"request_id": request_id}, "Request rejected.")


# ---------------------------------------------------------------------------
# Legacy / token verify (kept for backward compatibility)
# ---------------------------------------------------------------------------

async def verify_transfer_token(peer_token: str) -> dict:
    """
    Called by the sender peer before accepting an incoming STP connection.
    Validates the HMAC, checks expiry, and marks the token as used (one-time).
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
