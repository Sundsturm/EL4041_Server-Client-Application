"""
core/auth_manager.py
Manages access token (JWT), session token, and profile persistence.
"""

import os
import json
import time
from typing import Optional

from config import (
    ACCESS_JWT_PATH, SESSION_TOKEN_PATH, PROFILE_PATH,
    ACCESS_TOKEN_LIFETIME,
)


class AuthManager:
    """
    Handles all token/profile I/O for the desktop client.

    Stored files
    ────────────
    tokens/access.jwt      → raw JWT string
    tokens/session.token   → raw session token string (SESS_XXXXXXXX)
    profile/profile.json   → { username, user_id, ... }
    """

    def __init__(self):
        self._access_token: Optional[str]  = None
        self._session_token: Optional[str] = None
        self._profile: dict = {}
        self._access_issued_at: float = 0.0
        self._load_all()

    # ─── Internal helpers ────────────────────────────────────────────────────

    def _ensure_dirs(self):
        for path in (ACCESS_JWT_PATH, SESSION_TOKEN_PATH, PROFILE_PATH):
            os.makedirs(os.path.dirname(path), exist_ok=True)

    def _load_all(self):
        self._ensure_dirs()
        # Access JWT
        if os.path.exists(ACCESS_JWT_PATH):
            with open(ACCESS_JWT_PATH, "r") as f:
                self._access_token = f.read().strip() or None
        # Session token
        if os.path.exists(SESSION_TOKEN_PATH):
            with open(SESSION_TOKEN_PATH, "r") as f:
                data = f.read().strip()
                if data:
                    try:
                        obj = json.loads(data)
                        self._session_token = obj.get("token")
                        self._access_issued_at = obj.get("issued_at", 0.0)
                    except json.JSONDecodeError:
                        self._session_token = data
        # Profile
        if os.path.exists(PROFILE_PATH):
            with open(PROFILE_PATH, "r") as f:
                try:
                    self._profile = json.load(f)
                except json.JSONDecodeError:
                    self._profile = {}

    # ─── Access Token ────────────────────────────────────────────────────────

    def save_access_token(self, token: str):
        self._access_token = token
        self._access_issued_at = time.time()
        with open(ACCESS_JWT_PATH, "w") as f:
            f.write(token)

    def get_access_token(self) -> Optional[str]:
        return self._access_token

    def is_access_token_expired(self) -> bool:
        if not self._access_token:
            return True
        elapsed = time.time() - self._access_issued_at
        # Treat as expired 30s before actual expiry to avoid race conditions
        return elapsed >= (ACCESS_TOKEN_LIFETIME - 30)

    def clear_access_token(self):
        self._access_token = None
        if os.path.exists(ACCESS_JWT_PATH):
            os.remove(ACCESS_JWT_PATH)

    # ─── Session Token ───────────────────────────────────────────────────────

    def save_session_token(self, token: str):
        self._session_token = token
        obj = {"token": token, "issued_at": time.time()}
        with open(SESSION_TOKEN_PATH, "w") as f:
            json.dump(obj, f)

    def get_session_token(self) -> Optional[str]:
        return self._session_token

    def clear_session_token(self):
        self._session_token = None
        if os.path.exists(SESSION_TOKEN_PATH):
            os.remove(SESSION_TOKEN_PATH)

    # ─── Profile ─────────────────────────────────────────────────────────────

    def save_profile(self, profile: dict):
        self._profile = profile
        with open(PROFILE_PATH, "w") as f:
            json.dump(profile, f, indent=2)

    def get_profile(self) -> dict:
        return self._profile

    def get_username(self) -> str:
        return self._profile.get("username", "")

    def get_user_id(self) -> Optional[str]:
        return self._profile.get("user_id")

    # ─── Lifecycle ───────────────────────────────────────────────────────────

    def is_logged_in(self) -> bool:
        return bool(self._session_token)

    def logout(self):
        self.clear_access_token()
        self.clear_session_token()
        self._profile = {}
        if os.path.exists(PROFILE_PATH):
            os.remove(PROFILE_PATH)
