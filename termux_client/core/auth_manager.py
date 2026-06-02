"""
core/auth_manager.py
Stores access token, session token, and local profile data.
"""

from __future__ import annotations

from typing import Any

from config import ACCESS_TOKEN_FILE, SESSION_TOKEN_FILE, PROFILE_FILE
from core.local_storage import read_text, write_text, delete_file, read_json, write_json


class AuthManager:
    def get_access_token(self) -> str:
        return read_text(ACCESS_TOKEN_FILE)

    def save_access_token(self, token: str) -> None:
        write_text(ACCESS_TOKEN_FILE, token)

    def get_session_token(self) -> str:
        return read_text(SESSION_TOKEN_FILE)

    def save_session_token(self, token: str) -> None:
        write_text(SESSION_TOKEN_FILE, token)

    def get_profile(self) -> dict[str, Any]:
        return read_json(PROFILE_FILE, {}) or {}

    def save_profile(self, profile: dict[str, Any]) -> None:
        write_json(PROFILE_FILE, profile)

    def get_username(self) -> str:
        return str(self.get_profile().get("username", ""))

    def get_user_id(self) -> str:
        return str(self.get_profile().get("user_id", ""))

    def is_logged_in(self) -> bool:
        return bool(self.get_access_token() or self.get_session_token())

    def logout_local(self) -> None:
        delete_file(ACCESS_TOKEN_FILE)
        delete_file(SESSION_TOKEN_FILE)
        delete_file(PROFILE_FILE)
