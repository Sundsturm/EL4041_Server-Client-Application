"""
server/database.py
------------------
Database layer: schema initialisation and low-level async helpers.
All access goes through aiosqlite so the asyncio event loop is never blocked.
"""

import asyncio
import aiosqlite
from server import config

# Module-level connection pool (single connection, shared across the process).
_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    """Return the open database connection, opening it if necessary."""
    global _db
    if _db is None:
        _db = await aiosqlite.connect(config.DB_PATH)
        _db.row_factory = aiosqlite.Row   # rows behave like dicts
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
    return _db


async def close_db() -> None:
    """Close the database connection (called on server shutdown)."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def init_db() -> None:
    """Create all tables (idempotent – safe to call on every startup)."""
    db = await get_db()
    await db.executescript(
        """
        -- ----------------------------------------------------------------
        -- users: account credentials
        -- ----------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS users (
            user_id      TEXT PRIMARY KEY,
            username     TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at   TEXT NOT NULL
        );

        -- ----------------------------------------------------------------
        -- profiles: optional display info
        -- ----------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS profiles (
            user_id      TEXT PRIMARY KEY REFERENCES users(user_id),
            display_name TEXT,
            bio          TEXT
        );

        -- ----------------------------------------------------------------
        -- sessions: long-lived session tokens + per-session JWT secrets
        -- ----------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS sessions (
            session_id  TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL REFERENCES users(user_id),
            token       TEXT UNIQUE NOT NULL,
            jwt_secret  TEXT,          -- NULL after logout/revocation
            expires_at  TEXT NOT NULL,
            revoked     INTEGER NOT NULL DEFAULT 0
        );

        -- ----------------------------------------------------------------
        -- peer_registry: online peers and their Tailscale addresses
        -- ----------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS peer_registry (
            peer_id       TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL REFERENCES users(user_id),
            tailscale_ip  TEXT NOT NULL,
            port          INTEGER NOT NULL,
            status        TEXT NOT NULL DEFAULT 'offline',
            last_seen     TEXT NOT NULL
        );

        -- ----------------------------------------------------------------
        -- music_metadata: published song catalogue
        -- ----------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS music_metadata (
            music_id     TEXT PRIMARY KEY,
            owner_id     TEXT NOT NULL REFERENCES users(user_id),
            filename     TEXT NOT NULL,
            mime_type    TEXT NOT NULL,
            size         INTEGER NOT NULL,
            hmac_hash    TEXT NOT NULL,    -- HMAC-SHA256 of file bytes
            published_at TEXT NOT NULL
        );

        -- ----------------------------------------------------------------
        -- publish_history
        -- ----------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS publish_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT NOT NULL REFERENCES users(user_id),
            music_id  TEXT NOT NULL REFERENCES music_metadata(music_id),
            timestamp TEXT NOT NULL
        );

        -- ----------------------------------------------------------------
        -- download_history
        -- ----------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS download_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_id  TEXT NOT NULL REFERENCES users(user_id),
            music_id      TEXT NOT NULL,
            peer_id       TEXT,
            timestamp     TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'pending'
        );

        -- ----------------------------------------------------------------
        -- peer_tokens: short-lived P2P transfer authorisation tokens
        -- ----------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS peer_tokens (
            token        TEXT PRIMARY KEY,
            music_id     TEXT NOT NULL,
            requester_id TEXT NOT NULL,
            provider_id  TEXT NOT NULL,
            expires_at   TEXT NOT NULL,
            used         INTEGER NOT NULL DEFAULT 0,
            hmac_sig     TEXT NOT NULL    -- HMAC-SHA256 of token payload
        );

        -- ----------------------------------------------------------------
        -- transfer_negotiation: metadata about each negotiated transfer
        -- ----------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS transfer_negotiation (
            negotiation_id TEXT PRIMARY KEY,
            peer_token     TEXT NOT NULL REFERENCES peer_tokens(token),
            peer_ip        TEXT NOT NULL,
            peer_port      INTEGER NOT NULL,
            status         TEXT NOT NULL DEFAULT 'pending'
        );

        -- ----------------------------------------------------------------
        -- logs: structured server event log
        -- ----------------------------------------------------------------
        CREATE TABLE IF NOT EXISTS logs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            level     TEXT NOT NULL,
            source    TEXT NOT NULL,
            message   TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        """
    )
    await db.commit()
