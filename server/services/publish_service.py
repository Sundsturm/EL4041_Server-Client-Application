"""
server/services/publish_service.py
------------------------------------
Song metadata publication and search.
Song files are NOT stored here -- only metadata.
The HMAC-SHA256 hash is computed client-side and stored as-is.
"""

import uuid
from datetime import datetime, timezone

from server.database import get_db
from server.models.schemas import err, ok
from server.services import logging_service


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


ALLOWED_MIME_TYPES = {
    "audio/mpeg",       # mp3
    "audio/flac",       # flac
    "audio/wav",        # wav
    "audio/aac",        # aac
    "audio/ogg",        # ogg
    "audio/mp4",        # m4a
    "audio/x-m4a",
}


async def publish(user_id: str, metadata: dict) -> dict:
    """
    Publish song metadata to the catalogue.

    Expected metadata keys: filename, mime_type, size, hmac_hash, stp_port.
    Optional metadata keys: title, artist, album.
    Returns ok with the generated music_id.
    """
    filename  = metadata.get("filename", "")
    mime_type = metadata.get("mime_type", "")
    size      = metadata.get("size", 0)
    hmac_hash = metadata.get("hmac_hash", "")
    stp_port  = metadata.get("stp_port", 0)
    title     = metadata.get("title", "")
    artist    = metadata.get("artist", "")
    album     = metadata.get("album", "")

    if not filename or not hmac_hash:
        return err("filename and hmac_hash are required.")
    if mime_type not in ALLOWED_MIME_TYPES:
        return err(f"Unsupported mime_type '{mime_type}'.")

    music_id = str(uuid.uuid4())
    now = _utcnow_iso()

    db = await get_db()
    await db.execute(
        """
        INSERT INTO music_metadata
            (music_id, owner_id, filename, mime_type, size, hmac_hash, published_at,
             title, artist, album)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (music_id, user_id, filename, mime_type, size, hmac_hash, now,
         title, artist, album),
    )
    await db.execute(
        "INSERT INTO publish_history (user_id, music_id, timestamp) VALUES (?,?,?)",
        (user_id, music_id, now),
    )
    await db.commit()

    await logging_service.log_publish(user_id, music_id, filename)
    return ok({"music_id": music_id, "stp_port": stp_port}, "Song published.")


async def search(query: str) -> dict:
    """
    Full-text search on filename, title, and artist (case-insensitive LIKE).
    Returns a list of song metadata dicts.
    """
    db = await get_db()
    pattern = f"%{query}%"
    async with db.execute(
        """
        SELECT mm.music_id, mm.filename, mm.mime_type, mm.size,
               mm.hmac_hash, mm.published_at, mm.title, mm.artist, mm.album,
               u.username AS owner
        FROM music_metadata mm
        JOIN users u ON u.user_id = mm.owner_id
        WHERE mm.filename LIKE ?
           OR mm.title    LIKE ?
           OR mm.artist   LIKE ?
        ORDER BY mm.published_at DESC
        LIMIT 50
        """,
        (pattern, pattern, pattern),
    ) as cur:
        rows = await cur.fetchall()

    return ok({"songs": [dict(r) for r in rows]})


async def get_song(music_id: str) -> dict:
    """Fetch a single song's metadata."""
    db = await get_db()
    async with db.execute(
        """
        SELECT mm.music_id, mm.filename, mm.mime_type, mm.size,
               mm.hmac_hash, mm.published_at, mm.title, mm.artist, mm.album,
               u.username AS owner, mm.owner_id
        FROM music_metadata mm
        JOIN users u ON u.user_id = mm.owner_id
        WHERE mm.music_id = ?
        """,
        (music_id,),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        return err(f"Song '{music_id}' not found.")

    return ok(dict(row))
