"""
config.py
Global configuration for Desktop Client.
"""

import os

# ── Server ──────────────────────────────────────────────────────────────────
SERVER_BASE_URL = os.environ.get("SERVER_BASE_URL", "https://localhost:8443")

# ── Local storage paths ──────────────────────────────────────────────────────
BASE_DIR        = os.path.join(os.path.dirname(__file__), "client")
PROFILE_PATH    = os.path.join(BASE_DIR, "profile", "profile.json")
ACCESS_JWT_PATH = os.path.join(BASE_DIR, "tokens", "access.jwt")
SESSION_TOKEN_PATH = os.path.join(BASE_DIR, "tokens", "session.token")
SETTINGS_PATH   = os.path.join(BASE_DIR, "settings", "config.json")
HISTORY_PATH    = os.path.join(BASE_DIR, "history", "history.json")
MUSIC_DIR       = os.path.join(BASE_DIR, "music")

# ── STP (Song Transfer Protocol) ─────────────────────────────────────────────
STP_LISTEN_PORT      = 55000          # Port this desktop client listens for incoming STP
STP_DEFAULT_CHUNK_KB = 64             # KB per chunk
STP_VERSION          = "1.0"

SUPPORTED_MIME_TYPES = {
    ".mp3":  "audio/mpeg",
    ".flac": "audio/flac",
    ".wav":  "audio/wav",
    ".aac":  "audio/aac",
    ".ogg":  "audio/ogg",
    ".m4a":  "audio/mp4",
}

# ── Token lifetimes (seconds) ─────────────────────────────────────────────────
ACCESS_TOKEN_LIFETIME  = 12 * 60       # 12 minutes
SESSION_TOKEN_LIFETIME = 7 * 24 * 3600 # 7 days

# ── TLS ──────────────────────────────────────────────────────────────────────
# Set to False only for local dev with self-signed certs
TLS_VERIFY = os.environ.get("TLS_VERIFY", "false").lower() != "false"
