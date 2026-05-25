"""
server/services/session_service.py
------------------------------------
Handles session lifecycle: refresh (issue new access token), verify,
and background expiry cleanup.
"""

import asyncio
from datetime import datetime, timezone

from server import config
from server.database import get_db
from server.models.schemas import err, ok
from server.security import jwt_handler, token_store


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


async def refresh(session_token: str) -> dict:
    """
    Re-issue a JWT access token for an existing valid session.
    The session's jwt_secret is reused (no new session row created).
    """
    info = await token_store.verify_session_token(session_token)
    if info is None:
        return err("Session invalid or expired. Please log in again.")

    db = await get_db()
    async with db.execute(
        "SELECT username FROM users WHERE user_id=?", (info.user_id,)
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        return err("User not found.")

    access_token = jwt_handler.generate_access_token(
        info.user_id, row["username"], info.jwt_secret
    )
    return ok({"access_token": access_token}, "Token refreshed.")


async def verify_session(session_token: str) -> dict:
    """
    Validate a session token and return user_id + jwt_secret.
    Used internally (e.g. by REST middleware) and exposed as SESSION_VERIFY
    for CSP clients.
    """
    info = await token_store.verify_session_token(session_token)
    if info is None:
        return err("Session invalid or expired.")

    return ok(
        {"user_id": info.user_id, "jwt_secret": info.jwt_secret},
        "Session valid.",
    )


async def expire_stale_sessions() -> None:
    """
    Mark expired, non-revoked sessions as revoked and NULL their jwt_secrets.
    Called by the background cleanup task.
    """
    now = _utcnow_iso()
    db = await get_db()
    await db.execute(
        """
        UPDATE sessions
        SET revoked=1, jwt_secret=NULL
        WHERE revoked=0 AND expires_at < ?
        """,
        (now,),
    )
    await db.commit()


async def cleanup_loop() -> None:
    """
    Background coroutine that periodically expires stale sessions.
    Run as an asyncio task in main.py.
    """
    while True:
        await asyncio.sleep(config.SESSION_CLEANUP_INTERVAL)
        await expire_stale_sessions()
