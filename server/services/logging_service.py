"""
server/services/logging_service.py
------------------------------------
Structured server-side event logging.

Setiap peristiwa dicatat ke DUA tempat secara real-time:
  1. Konsol  — via Python stdlib `logging` (tampil langsung saat terjadi)
  2. Database — tabel `logs` (persisten untuk audit trail)

Helper functions tersedia per jenis interaksi sehingga service lain
cukup memanggil satu fungsi deskriptif tanpa perlu merangkai pesan sendiri.
"""

import logging
import sys
from datetime import datetime, timezone

from server.database import get_db
from server.models.schemas import err, ok

# ---------------------------------------------------------------------------
# Konfigurasi Python stdlib logger
# ---------------------------------------------------------------------------
# Format: [LEVEL]  YYYY-MM-DDTHH:MM:SS+00:00  [source]  pesan
_LOG_FMT = "%(levelname)-8s  %(asctime)s  [%(name)s]  %(message)s"
_DATE_FMT = "%Y-%m-%dT%H:%M:%S%z"

logging.basicConfig(
    level=logging.DEBUG,
    format=_LOG_FMT,
    datefmt=_DATE_FMT,
    stream=sys.stdout,
    force=True,          # pastikan config ini menggantikan default Uvicorn
)

# Logger utama server — sub-logger per modul bisa dibuat via get_logger()
_root_logger = logging.getLogger("server")


def get_logger(name: str) -> logging.Logger:
    """Kembalikan child logger bernama 'server.<name>' untuk modul pemanggil."""
    return _root_logger.getChild(name)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

_LEVEL_MAP = {
    "DEBUG":    logging.DEBUG,
    "INFO":     logging.INFO,
    "WARNING":  logging.WARNING,
    "ERROR":    logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


# ---------------------------------------------------------------------------
# Core log function — tulis ke konsol + DB sekaligus
# ---------------------------------------------------------------------------

async def log(level: str, source: str, message: str) -> None:
    """
    Catat satu entri log ke konsol (real-time) DAN ke tabel `logs` (DB).

    Parameters
    ----------
    level   : "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL"
    source  : nama modul/konteks, contoh: "auth", "publish", "peer"
    message : teks bebas yang mendeskripsikan kejadian
    """
    level = level.upper() if level.upper() in VALID_LEVELS else "INFO"

    # 1. Cetak ke konsol secara real-time
    _root_logger.getChild(source).log(_LEVEL_MAP[level], message)

    # 2. Simpan ke database
    db = await get_db()
    await db.execute(
        "INSERT INTO logs (level, source, message, timestamp) VALUES (?,?,?,?)",
        (level, source, message, _utcnow_iso()),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Domain-specific helper functions
# (service lain cukup import dan panggil fungsi ini)
# ---------------------------------------------------------------------------

async def log_register(username: str, user_id: str, success: bool,
                       reason: str = "") -> None:
    """Log percobaan registrasi akun baru."""
    if success:
        await log("INFO", "auth",
                  f"REGISTER OK   | user={username!r}  id={user_id}")
    else:
        await log("WARNING", "auth",
                  f"REGISTER FAIL | user={username!r}  reason={reason!r}")


async def log_login(username: str, user_id: str, success: bool,
                    reason: str = "") -> None:
    """Log percobaan login."""
    if success:
        await log("INFO", "auth",
                  f"LOGIN OK      | user={username!r}  id={user_id}")
    else:
        await log("WARNING", "auth",
                  f"LOGIN FAIL    | user={username!r}  reason={reason!r}")


async def log_logout(user_id: str, session_token_prefix: str = "") -> None:
    """Log logout / pencabutan sesi."""
    hint = f"  session=...{session_token_prefix[-6:]}" if session_token_prefix else ""
    await log("INFO", "auth",
              f"LOGOUT        | id={user_id}{hint}")


async def log_publish(user_id: str, music_id: str, filename: str) -> None:
    """Log penambahan metadata lagu ke katalog (database bertambah)."""
    await log("INFO", "publish",
              f"PUBLISH       | user={user_id}  music={music_id}  file={filename!r}")


async def log_edit(user_id: str, music_id: str, fields: str) -> None:
    """Log pengeditan metadata lagu (database diedit)."""
    await log("INFO", "publish",
              f"EDIT          | user={user_id}  music={music_id}  fields={fields}")


async def log_delete(user_id: str, music_id: str, filename: str = "") -> None:
    """Log penghapusan lagu dari katalog (database dihapus)."""
    await log("WARNING", "publish",
              f"DELETE        | user={user_id}  music={music_id}  file={filename!r}")


async def log_peer(event: str, user_id: str, peer_id: str,
                   ip: str = "", port: int = 0) -> None:
    """Log perubahan status peer (register / unregister / heartbeat)."""
    detail = f"  ip={ip}:{port}" if ip else ""
    await log("INFO", "peer",
              f"{event.upper():<14}| user={user_id}  peer={peer_id}{detail}")


async def log_session(event: str, user_id: str, detail: str = "") -> None:
    """Log event sesi: refresh token, expiry cleanup, dll."""
    await log("INFO", "session",
              f"{event.upper():<14}| user={user_id}  {detail}".rstrip())


# ---------------------------------------------------------------------------
# Public API — dipakai message router untuk LOG_REQ dari client
# ---------------------------------------------------------------------------

async def log_request(level: str, source: str, message: str) -> dict:
    """
    Public-facing version of log() yang mengembalikan APIResponse dict.
    Dipakai message router untuk pesan LOG_REQ (CSP/REST).
    """
    await log(level, source, message)
    return ok(message="Log entry recorded.")


# ---------------------------------------------------------------------------
# History retrieval
# ---------------------------------------------------------------------------

async def get_history(user_id: str, history_type: str = "download") -> dict:
    """
    Ambil riwayat aktivitas untuk satu user.

    history_type
    ------------
    "download"  – baris dari download_history
    "publish"   – baris dari publish_history
    "login"     – baris dari sessions (event login)
    "logs"      – baris dari tabel logs (server-side audit trail)
    """
    db = await get_db()

    if history_type == "download":
        async with db.execute(
            """
            SELECT dr.request_id, dr.music_id,
                   mm.filename, mm.title, mm.artist,
                   dr.status, dr.created_at, dr.updated_at
            FROM download_requests dr
            LEFT JOIN music_metadata mm ON mm.music_id = dr.music_id
            WHERE dr.requester_id = ?
              AND dr.status IN ('approved', 'in_progress', 'completed', 'failed')
            ORDER BY dr.updated_at DESC
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

    elif history_type == "logs":
        async with db.execute(
            """
            SELECT id, level, source, message, timestamp
            FROM logs
            ORDER BY timestamp DESC
            LIMIT 200
            """,
        ) as cur:
            rows = await cur.fetchall()

    else:
        return err(f"Unknown history_type '{history_type}'.")

    return ok({"history": [dict(r) for r in rows]})
