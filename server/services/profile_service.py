"""
server/services/profile_service.py
------------------------------------
User profile management: read, update, and account deletion.
"""

import bcrypt

from server.database import get_db
from server.models.schemas import err, ok
from server.services import logging_service


async def get_profile(user_id: str) -> dict:
    """
    GET /profile
    Returns the user's profile data (user_id, username, bio, created_at).
    display_name is removed — username is the single name field.
    """
    db = await get_db()

    async with db.execute(
        """
        SELECT u.user_id, u.username, u.created_at, p.bio
        FROM users u
        LEFT JOIN profiles p ON p.user_id = u.user_id
        WHERE u.user_id = ?
        """,
        (user_id,),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        return err("User not found.")

    return ok(dict(row))


async def update_profile(
    user_id: str,
    username: str = "",
    bio: str = "",
    password: str = "",
) -> dict:
    """
    POST /profile/update
    Updates username (in users table) and/or bio (in profiles table).
    Optionally changes the password.
    display_name is removed; username is the single name field.
    """
    db = await get_db()

    # Update username in users table (if provided)
    if username:
        # Check uniqueness before updating
        async with db.execute(
            "SELECT user_id FROM users WHERE username = ? AND user_id != ?",
            (username, user_id),
        ) as cur:
            conflict = await cur.fetchone()
        if conflict:
            return err(f"Username '{username}' is already taken.")

        await db.execute(
            "UPDATE users SET username = ? WHERE user_id = ?",
            (username, user_id),
        )

    # Update bio in profiles table (if provided)
    if bio is not None and bio != "":
        await db.execute(
            """
            INSERT INTO profiles (user_id, bio)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET bio = excluded.bio
            """,
            (user_id, bio),
        )

    # If a new password was supplied, hash and store it
    if password:
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        await db.execute(
            "UPDATE users SET password_hash = ? WHERE user_id = ?",
            (pw_hash, user_id),
        )

    await db.commit()

    await logging_service.log(
        "INFO", "profile",
        f"PROFILE UPDATE | user_id={user_id} | username_changed={bool(username)} | password_changed={bool(password)}"
    )
    return ok(message="Profile updated successfully.")


async def delete_account(user_id: str, password: str) -> dict:
    """
    POST /profile/delete
    Verifies the user's password, then permanently deletes the account
    and all associated data in the correct foreign-key order.
    """
    db = await get_db()

    # 1. Verify password first
    async with db.execute(
        "SELECT password_hash, username FROM users WHERE user_id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        return err("User not found.")

    if not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        await logging_service.log(
            "WARNING", "profile",
            f"DELETE FAIL | user_id={user_id} | reason=Wrong password"
        )
        return err("Incorrect password.")

    username = row["username"]

    # 2. Delete in child-to-parent order (respects FK constraints)

    # transfer_negotiation → references peer_tokens
    await db.execute(
        """
        DELETE FROM transfer_negotiation
        WHERE peer_token IN (
            SELECT token FROM peer_tokens WHERE requester_id = ? OR provider_id = ?
        )
        """,
        (user_id, user_id),
    )

    # peer_tokens
    await db.execute(
        "DELETE FROM peer_tokens WHERE requester_id = ? OR provider_id = ?",
        (user_id, user_id),
    )

    # download_history
    await db.execute(
        "DELETE FROM download_history WHERE requester_id = ?",
        (user_id,),
    )

    # publish_history → references music_metadata
    await db.execute(
        "DELETE FROM publish_history WHERE user_id = ?",
        (user_id,),
    )

    # download_requests → references users(requester_id, provider_id) and music_metadata(music_id)
    await db.execute(
        "DELETE FROM download_requests WHERE requester_id = ? OR provider_id = ?",
        (user_id, user_id),
    )

    # music_metadata
    await db.execute(
        "DELETE FROM music_metadata WHERE owner_id = ?",
        (user_id,),
    )

    # peer_registry
    await db.execute(
        "DELETE FROM peer_registry WHERE user_id = ?",
        (user_id,),
    )

    # sessions
    await db.execute(
        "DELETE FROM sessions WHERE user_id = ?",
        (user_id,),
    )

    # profiles
    await db.execute(
        "DELETE FROM profiles WHERE user_id = ?",
        (user_id,),
    )

    # users — last, as everything else references it
    await db.execute(
        "DELETE FROM users WHERE user_id = ?",
        (user_id,),
    )

    await db.commit()

    await logging_service.log(
        "INFO", "profile",
        f"ACCOUNT DELETE | user_id={user_id} | username={username}"
    )
    return ok(message="Account deleted successfully.")
