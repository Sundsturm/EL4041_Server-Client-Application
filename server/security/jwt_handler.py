"""
server/security/jwt_handler.py
-------------------------------
Per-session JWT access token generation and verification.

Design: Each login session gets its own randomly-generated signing secret
stored in the `sessions` table. Revoking a session NULLs the jwt_secret,
making all tokens from that session instantly invalid -- no blocklist needed.
"""

import secrets
from datetime import datetime, timedelta, timezone

import jwt

from server import config


def new_session_secret() -> str:
    """Generate a cryptographically random 32-byte hex secret for a session."""
    return secrets.token_hex(config.JWT_SECRET_BYTES)


def generate_access_token(user_id: str, username: str, session_secret: str) -> str:
    """
    Issue a JWT access token signed with the session-specific secret.

    Parameters
    ----------
    user_id:        UUID of the authenticated user.
    username:       Human-readable username (embedded as claim for convenience).
    session_secret: Per-session HMAC key stored in the sessions table.

    Returns
    -------
    Encoded JWT string.
    """
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "iat": now,
        "exp": now + timedelta(minutes=config.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, session_secret, algorithm=config.JWT_ALGORITHM)


def verify_access_token(token: str, session_secret: str) -> dict | None:
    """
    Decode and validate a JWT access token.

    Parameters
    ----------
    token:          The raw JWT string from the Authorization header.
    session_secret: The signing secret fetched from the sessions table for
                    this token's session.  Must match the secret used at
                    issuance time.

    Returns
    -------
    Decoded payload dict on success, or None if the token is invalid/expired.
    """
    try:
        payload = jwt.decode(
            token,
            session_secret,
            algorithms=[config.JWT_ALGORITHM],
        )
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
