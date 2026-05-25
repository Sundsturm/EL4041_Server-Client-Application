"""
server/services/logging_service.py
------------------------------------
Structured server-side event logging (into the `logs` table)
and history retrieval (login / publish / download).
"""

from datetime import datetime, timezone

from server.database import get_db
from server.models.schemas import err, ok


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


async def log(level: str, source: str, message: str) -> None:
    """
    Append a structured log entry to the `logs` table.
    This is fire-and-forget; callers do not need to await a meaningful result.
    """
    level = level.upper() if level.upper() in VALID_LEVELS else "INFO"
    db = await get_db()
    await db.execute(
        "INSERT INTO logs (level, source, message, timestamp) VALUES (?,?,?,?)",
        (level, source, message, _utcnow_iso()),
    )
    await db.commit()


async def log_request(level: str, source: str, message: str) -> dict:
    """
    Public-facing version of log() that returns an APIResponse dict.
    Used by the message router for LOG_REQ CSP messages.
    """
    await log(level, source, message)
    return ok(message="Log entry recorded.")


async def get_history(user_id: str, history_type: str = "download") -> dict:
    """
    Retrieve history for a user.

    history_type
    ------------
    "download"  – rows from download_history
    "publish"   – rows from publish_history
    "login"     – rows from sessions (login events)
    """
    db = await get_db()

    if history_type == "download":
        async with db.execute(
            """
            SELECT dh.id, dh.music_id, mm.filename, dh.peer_id,
                   dh.timestamp, dh.status
            FROM download_history dh
            LEFT JOIN music_metadata mm ON mm.music_id = dh.music_id
            WHERE dh.requester_id = ?
            ORDER BY dh.timestamp DESC
            LIMIT 100
            """,
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()

    elif history_type == "publish":
        async with db.execute(
            """
            SELECT ph.id, ph.music_id, mm.filename, ph.timestamp
            FROM publish_history ph
            LEFT JOIN music_metadata mm ON mm.music_id = ph.music_id
            WHERE ph.user_id = ?
            ORDER BY ph.timestamp DESC
            LIMIT 100
            """,
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()

    elif history_type == "login":
        async with db.execute(
            """
            SELECT session_id, expires_at, revoked
            FROM sessions
            WHERE user_id = ?
            ORDER BY expires_at DESC
            LIMIT 50
            """,
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()

    else:
        return err(f"Unknown history_type '{history_type}'.")

    return ok({"history": [dict(r) for r in rows]})
