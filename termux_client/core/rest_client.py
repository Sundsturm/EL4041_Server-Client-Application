"""
core/rest_client.py
REST fallback client for development/testing.
The main target for Android is CSP over QUIC, but this helps test quickly.
"""

from __future__ import annotations

import httpx
from typing import Any

from config import SERVER_REST_BASE_URL, TLS_VERIFY
from core.auth_manager import AuthManager


class RESTError(Exception):
    pass


class RESTClient:
    def __init__(self, auth: AuthManager):
        self.auth = auth
        self.client = httpx.AsyncClient(
            base_url=SERVER_REST_BASE_URL.rstrip("/"),
            verify=TLS_VERIFY,
            timeout=30.0,
        )

    def _headers(self) -> dict:
        token = self.auth.get_access_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def _unwrap(resp: httpx.Response) -> Any:
        try:
            body = resp.json()
        except Exception:
            raise RESTError(resp.text)

        if not resp.is_success:
            raise RESTError(body.get("detail") or body.get("message") or str(body))

        if isinstance(body, dict) and "status" in body:
            if body.get("status") == "error":
                raise RESTError(body.get("message") or str(body))
            return body.get("data") or {}
        return body

    async def register(self, username: str, password: str, display_name: str = "") -> dict:
        resp = await self.client.post("/register", json={
            "username": username,
            "password": password,
            "display_name": display_name or username,
        })
        return self._unwrap(resp)

    async def login(self, username: str, password: str) -> dict:
        resp = await self.client.post("/login", json={"username": username, "password": password})
        data = self._unwrap(resp)
        self.auth.save_access_token(data["access_token"])
        self.auth.save_session_token(data["session_token"])
        self.auth.save_profile({"user_id": data.get("user_id"), "username": username})
        return data

    async def logout(self) -> dict:
        session = self.auth.get_session_token()
        resp = await self.client.post("/logout", json={"session_token": session}, headers=self._headers())
        data = self._unwrap(resp)
        self.auth.logout_local()
        return data

    async def publish(self, metadata: dict) -> dict:
        resp = await self.client.post("/publish", json=metadata, headers=self._headers())
        return self._unwrap(resp)

    async def search(self, query: str, limit: int = 50) -> dict:
        resp = await self.client.get("/songs", params={"q": query, "limit": limit}, headers=self._headers())
        return self._unwrap(resp)

    async def download(self, music_id: str) -> dict:
        resp = await self.client.post("/download", json={"music_id": music_id}, headers=self._headers())
        return self._unwrap(resp)

    async def history(self, history_type: str = "download") -> dict:
        resp = await self.client.get("/history", params={"history_type": history_type}, headers=self._headers())
        return self._unwrap(resp)
