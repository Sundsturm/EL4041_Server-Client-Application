"""
termux_client/config.py
Central configuration for Android/Termux CLI client.
"""

from pathlib import Path

# Change this to your server Tailscale IP / MagicDNS / LAN IP.
SERVER_HOST = "100.98.237.27"

# QUIC/CSP server port, based on the project context.
SERVER_QUIC_PORT = 4433

# REST fallback URL for development/testing.
SERVER_REST_BASE_URL = "https://100.98.237.27:8443"
TLS_VERIFY = False

# STP file transfer config.
STP_LISTEN_HOST = "0.0.0.0"
STP_LISTEN_PORT = 5050
STP_CHUNK_SIZE = 64 * 1024

# Heartbeat / server-monitor config.
HEARTBEAT_INTERVAL   = 30   # seconds between each ping
HEARTBEAT_MAX_FAILS  = 3    # consecutive failures before auto-logout

# Local storage folders.
BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "profile"
TOKENS_DIR = BASE_DIR / "tokens"
HISTORY_DIR = BASE_DIR / "history"
MUSIC_DIR = BASE_DIR / "music"
DOWNLOAD_DIR = MUSIC_DIR / "downloads"

ACCESS_TOKEN_FILE = TOKENS_DIR / "access.jwt"
SESSION_TOKEN_FILE = TOKENS_DIR / "session.token"
PROFILE_FILE = PROFILE_DIR / "profile.json"
HISTORY_FILE = HISTORY_DIR / "history.json"
CATALOG_FILE = MUSIC_DIR / "catalog.json"      # music_id → local file path

SUPPORTED_AUDIO_EXTENSIONS = {
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
}
