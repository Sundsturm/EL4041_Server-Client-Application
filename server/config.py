"""
server/config.py
----------------
Central configuration for the Hybrid Music Sharing Server.
All tunable constants live here so the rest of the codebase
never hard-codes values.
"""

import os

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
REST_HOST: str = os.getenv("REST_HOST", "0.0.0.0")
REST_PORT: int = int(os.getenv("REST_PORT", "8443"))

QUIC_HOST: str = os.getenv("QUIC_HOST", "0.0.0.0")
QUIC_PORT: int = int(os.getenv("QUIC_PORT", "4433"))

# ---------------------------------------------------------------------------
# Tailscale / TLS
# ---------------------------------------------------------------------------
# Replace with the actual Tailscale MagicDNS hostname of the server machine.
# Run: tailscale status   to find your hostname.
TAILSCALE_HOSTNAME: str = os.getenv(
    "TAILSCALE_HOSTNAME", "laptop-8v06emvk.tail964715.ts.net"
)

CERT_PATH: str = os.getenv(
    "CERT_PATH", f"server/certs/{TAILSCALE_HOSTNAME}.crt"
)
KEY_PATH: str = os.getenv(
    "KEY_PATH", f"server/certs/{TAILSCALE_HOSTNAME}.key"
)

# All Tailscale IPs begin with this prefix.
TAILSCALE_IP_PREFIX: str = "100."

# ---------------------------------------------------------------------------
# QUIC
# ---------------------------------------------------------------------------
# Tailscale (WireGuard) adds ~60 bytes of overhead per UDP packet, reducing
# the effective MTU below the standard 1500.  Keeping datagrams at 1200 bytes
# avoids silent fragmentation or packet drops over the Tailnet.
QUIC_MAX_DATAGRAM_SIZE: int = 1200

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH: str = os.getenv("DB_PATH", "server.db")

# ---------------------------------------------------------------------------
# JWT (access tokens)
# ---------------------------------------------------------------------------
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "15"))

# Each session row stores its own randomly generated jwt_secret (32 hex bytes).
# This constant is only used as a fallback length reference, not as the actual secret.
JWT_SECRET_BYTES: int = 32

# ---------------------------------------------------------------------------
# Session tokens  (long-lived, stored in DB + client file)
# ---------------------------------------------------------------------------
SESSION_EXPIRE_DAYS: int = int(os.getenv("SESSION_EXPIRE_DAYS", "7"))

# ---------------------------------------------------------------------------
# Peer tokens  (short-lived, authorise one P2P STP transfer)
# ---------------------------------------------------------------------------
PEER_TOKEN_EXPIRE_SECONDS: int = int(os.getenv("PEER_TOKEN_EXPIRE_SECONDS", "300"))

# HMAC-SHA256 key used specifically for signing peer tokens.
# NOT related to JWT secrets (those are per-session).
HMAC_GLOBAL_SECRET: str = os.getenv("HMAC_GLOBAL_SECRET", "change-me-in-production")

# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------
# Interval (seconds) between runs of the session expiry cleanup coroutine.
SESSION_CLEANUP_INTERVAL: int = 60
