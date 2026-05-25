"""
server/security/token_store.py
--------------------------------
Helpers for session tokens and peer tokens, both backed by SQLite.

Session tokens (SESS_XXXXXXXX):
    Long-lived (7-14 days).  Used by clients to refresh access tokens without
    re-entering credentials.  Each session row also holds the per-session JWT
    signing secret.

Peer tokens (TRX_<random>.<hmac_sig>):
    Short-lived (1-5 minutes).  Authorise exactly one P2P STP transfer.
    The HMAC-SHA256 suffix lets the server do a fast cryptographic check
    before touching the database.
"""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from server import config
from server.database import get_db
from server.security.jwt_handler import new_session_secret


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _utc_iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------

@dataclass
class SessionInfo:
    session_id: str
    user_id: str
    jwt_secret: str


async def create_session_token(user_id: str) -> tuple[str, str, str]:
    """
    Create a new session for *user_id*.

    Returns
    -------
    (session_id, raw_token, jwt_secret)
        - session_id  – UUID for the sessions table PK.
        - raw_token   – "SESS_<16 uppercase hex>" stored in DB and sent to client.
        - jwt_secret  – random 32-byte hex; used to sign access tokens for this session.
    """
    session_id = str(uuid.uuid4())
    raw_token = "SESS_" + secrets.token_hex(8).upper()
    jwt_secret = new_session_secret()
    expires_at = _utc_iso(
        datetime.now(tz=timezone.utc) + timedelta(days=config.SESSION_EXPIRE_DAYS)
    )

    db = await get_db()
    await db.execute(
        """
        INSERT INTO sessions (session_id, user_id, token, jwt_secret, expires_at, revoked)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (session_id, user_id, raw_token, jwt_secret, expires_at),
    )
    await db.commit()
    return session_id, raw_token, jwt_secret


async def verify_session_token(token: str) -> SessionInfo | None:
    """
    Validate a session token.

    Returns
    -------
    SessionInfo if valid (not revoked, not expired), else None.
    """
    db = await get_db()
    async with db.execute(
        """
        SELECT session_id, user_id, jwt_secret, expires_at, revoked
        FROM sessions
        WHERE token = ?
        """,
        (token,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return None
    if row["revoked"]:
        return None
    if row["jwt_secret"] is None:
        return None

    expires_at = datetime.fromisoformat(row["expires_at"])
    if datetime.now(tz=timezone.utc) > expires_at:
        return None

    return SessionInfo(
        session_id=row["session_id"],
        user_id=row["user_id"],
        jwt_secret=row["jwt_secret"],
    )


async def revoke_session_token(token: str) -> None:
    """
    Revoke a session: set revoked=1 and NULL the jwt_secret.
    All access tokens signed with this session's secret are instantly invalid.
    """
    db = await get_db()
    await db.execute(
        "UPDATE sessions SET revoked=1, jwt_secret=NULL WHERE token=?",
        (token,),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Peer tokens
# ---------------------------------------------------------------------------

@dataclass
class PeerTokenInfo:
    token: str
    music_id: str
    requester_id: str
    provider_id: str


def _compute_peer_hmac(token_core: str) -> str:
    """Compute HMAC-SHA256 of *token_core* using HMAC_GLOBAL_SECRET."""
    return hmac.new(
        config.HMAC_GLOBAL_SECRET.encode(),
        token_core.encode(),
        hashlib.sha256,
    ).hexdigest()


async def create_peer_token(
    music_id: str,
    requester_id: str,
    provider_id: str,
) -> str:
    """
    Generate a signed peer token and persist it in the database.

    Token format: ``TRX_<16 hex chars>.<hmac_sha256_hex>``

    The HMAC suffix lets the server validate authenticity without a DB hit,
    then the DB is queried only to check expiry and the `used` flag.

    Returns
    -------
    The full token string to be sent to the requesting client.
    """
    token_core = "TRX_" + secrets.token_hex(8).upper()
    hmac_sig = _compute_peer_hmac(token_core)
    full_token = f"{token_core}.{hmac_sig}"

    expires_at = _utc_iso(
        datetime.now(tz=timezone.utc)
        + timedelta(seconds=config.PEER_TOKEN_EXPIRE_SECONDS)
    )

    db = await get_db()
    await db.execute(
        """
        INSERT INTO peer_tokens
            (token, music_id, requester_id, provider_id, expires_at, used, hmac_sig)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        """,
        (full_token, music_id, requester_id, provider_id, expires_at, hmac_sig),
    )
    await db.commit()
    return full_token


async def verify_peer_token(full_token: str) -> PeerTokenInfo | None:
    """
    Validate a peer token.

    Steps
    -----
    1. Split token into core + HMAC suffix.
    2. Recompute HMAC – fast cryptographic check before touching DB.
    3. Query DB for expiry and used flag.
    4. Return PeerTokenInfo on success; mark token as used.

    Returns
    -------
    PeerTokenInfo if valid, else None.
    """
    # --- Step 1: split ---
    parts = full_token.split(".", 1)
    if len(parts) != 2:
        return None
    token_core, provided_sig = parts

    # --- Step 2: HMAC check ---
    expected_sig = _compute_peer_hmac(token_core)
    if not hmac.compare_digest(expected_sig, provided_sig):
        return None

    # --- Step 3: DB lookup ---
    db = await get_db()
    async with db.execute(
        "SELECT music_id, requester_id, provider_id, expires_at, used FROM peer_tokens WHERE token=?",
        (full_token,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return None
    if row["used"]:
        return None

    expires_at = datetime.fromisoformat(row["expires_at"])
    if datetime.now(tz=timezone.utc) > expires_at:
        return None

    # --- Step 4: mark used ---
    await db.execute("UPDATE peer_tokens SET used=1 WHERE token=?", (full_token,))
    await db.commit()

    return PeerTokenInfo(
        token=full_token,
        music_id=row["music_id"],
        requester_id=row["requester_id"],
        provider_id=row["provider_id"],
    )
