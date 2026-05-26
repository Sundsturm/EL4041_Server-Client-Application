"""
server/services/auth_service.py
---------------------------------
User registration, login, and logout.
Passwords are hashed with bcrypt.
On login, a session row is created (with its own jwt_secret) and an
access token is issued immediately.
"""

import uuid
from datetime import datetime, timezone

import bcrypt

from server.database import get_db
from server.models.schemas import err, ok
from server.security import jwt_handler, token_store
from server.services import logging_service


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


async def register(username: str, password: str, display_name: str | None = None) -> dict:
    """
    Create a new user account.

    Returns ok with user_id, or error if username already taken.
    """
    db = await get_db()

    # Check uniqueness
    async with db.execute("SELECT user_id FROM users WHERE username=?", (username,)) as cur:
        if await cur.fetchone():
            await logging_service.log_register(username, "", success=False,
                                               reason="Username already taken")
            return err("Username already taken.")

    user_id = str(uuid.uuid4())
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    now = _utcnow_iso()

    await db.execute(
        "INSERT INTO users (user_id, username, password_hash, created_at) VALUES (?,?,?,?)",
        (user_id, username, pw_hash, now),
    )
    await db.execute(
        "INSERT INTO profiles (user_id, display_name, bio) VALUES (?,?,?)",
        (user_id, display_name or username, ""),
    )
    await db.commit()

    await logging_service.log_register(username, user_id, success=True)
    return ok({"user_id": user_id}, "Registration successful.")


async def login(username: str, password: str) -> dict:
    """
    Authenticate user. Returns access_token + session_token on success.
    """
    db = await get_db()

    async with db.execute(
        "SELECT user_id, password_hash FROM users WHERE username=?", (username,)
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        await logging_service.log_login(username, "", success=False,
                                        reason="User not found")
        return err("Invalid username or password.")

    if not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        await logging_service.log_login(username, row["user_id"], success=False,
                                        reason="Wrong password")
        return err("Invalid username or password.")

    user_id = row["user_id"]

    # Create session (generates jwt_secret internally)
    _session_id, session_token, jwt_secret = await token_store.create_session_token(user_id)

    # Issue access token
    access_token = jwt_handler.generate_access_token(user_id, username, jwt_secret)

    await logging_service.log_login(username, user_id, success=True)
    return ok(
        {
            "user_id": user_id,
            "access_token": access_token,
            "session_token": session_token,
        },
        "Login successful.",
    )


async def logout(session_token: str) -> dict:
    """
    Revoke the session.  All access tokens issued from this session become
    immediately invalid because the jwt_secret is NULLed in the DB.
    """
    info = await token_store.verify_session_token(session_token)
    if info is None:
        await logging_service.log("WARNING", "auth",
                                  "LOGOUT FAIL   | session not found or already revoked")
        return err("Session not found or already revoked.")

    await token_store.revoke_session_token(session_token)
    await logging_service.log_logout(info.user_id, session_token)
    return ok(message="Logged out successfully.")
