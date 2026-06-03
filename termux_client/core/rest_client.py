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

    # ─── Auth ─────────────────────────────────────────────────────────────────

    async def register(self, username: str, password: str) -> dict:
        resp = await self.client.post("/register", json={
            "username": username,
            "password": password,
        })
        return self._unwrap(resp)

    async def login(self, username: str, password: str) -> dict:
        resp = await self.client.post("/login", json={"username": username, "password": password})
        data = self._unwrap(resp)
        self.auth.save_access_token(data["access_token"])
        self.auth.save_session_token(data["session_token"])
        self.auth.save_profile({
            "user_id": data.get("user_id"),
            "username": username,
        })
        return data

    async def logout(self) -> dict:
        session = self.auth.get_session_token()
        resp = await self.client.post("/logout", json={"session_token": session}, headers=self._headers())
        data = self._unwrap(resp)
        self.auth.logout_local()
        return data

    # ─── Profile ─────────────────────────────────────────────────────────────

    async def get_profile(self) -> dict:
        """GET /profile — fetch full profile from server."""
        resp = await self.client.get("/profile", headers=self._headers())
        return self._unwrap(resp)

    async def update_profile(
        self,
        username: str = "",
        bio: str = "",
        password: str = "",
    ) -> dict:
        """POST /profile/update — update one or more profile fields."""
        body: dict = {}
        if username:
            body["username"] = username
        if bio:
            body["bio"] = bio
        if password:
            body["password"] = password
        resp = await self.client.post("/profile/update", json=body, headers=self._headers())
        return self._unwrap(resp)

    async def delete_profile(self, password: str) -> dict:
        """POST /profile/delete — delete account and sign out locally."""
        resp = await self.client.post(
            "/profile/delete",
            json={"password": password},
            headers=self._headers(),
        )
        data = self._unwrap(resp)
        self.auth.logout_local()
        return data

    # ─── Music ────────────────────────────────────────────────────────────────

    async def publish(self, metadata: dict) -> dict:
        resp = await self.client.post("/publish", json=metadata, headers=self._headers())
        return self._unwrap(resp)

    async def search(self, query: str, limit: int = 50) -> dict:
        resp = await self.client.get("/songs", params={"q": query, "limit": limit}, headers=self._headers())
        return self._unwrap(resp)

    async def list_songs(self, limit: int = 100) -> dict:
        """GET /songs/list — return all songs with full metadata (no filter)."""
        resp = await self.client.get("/songs/list", params={"limit": limit}, headers=self._headers())
        return self._unwrap(resp)

    async def download(self, music_id: str, requester_port: int = 5050) -> dict:
        resp = await self.client.post(
            "/download",
            json={"music_id": music_id, "requester_port": requester_port},
            headers=self._headers(),
        )
        return self._unwrap(resp)

    async def get_pending_requests(self, timeout: int = 28) -> dict:
        """Long-poll for pending download requests (owner side)."""
        resp = await self.client.get(
            "/transfer/requests",
            params={"timeout": timeout},
            headers=self._headers(),
            timeout=timeout + 5,
        )
        return self._unwrap(resp)

    async def approve_transfer(self, request_id: str) -> dict:
        resp = await self.client.post(
            "/transfer/approve",
            json={"request_id": request_id},
            headers=self._headers(),
        )
        return self._unwrap(resp)

    async def reject_transfer(self, request_id: str, reason: str = "") -> dict:
        resp = await self.client.post(
            "/transfer/reject",
            json={"request_id": request_id, "reason": reason},
            headers=self._headers(),
        )
        return self._unwrap(resp)

    async def get_transfer_status(self, request_id: str) -> dict:
        resp = await self.client.get(
            f"/transfer/status/{request_id}",
            headers=self._headers(),
        )
        return self._unwrap(resp)

    async def get_my_downloads(self) -> dict:
        resp = await self.client.get(
            "/transfer/my-downloads",
            headers=self._headers(),
        )
        return self._unwrap(resp)

    async def update_transfer_status(self, request_id: str, status: str) -> dict:
        resp = await self.client.post(
            "/transfer/update-status",
            json={"request_id": request_id, "status": status},
            headers=self._headers(),
        )
        return self._unwrap(resp)

    async def history(self, history_type: str = "download") -> dict:
        resp = await self.client.get("/history", params={"history_type": history_type}, headers=self._headers())
        return self._unwrap(resp)

    async def heartbeat(self) -> dict:
        """Lightweight ping: verifies server reachability and token validity."""
        resp = await self.client.get("/profile", headers=self._headers(), timeout=10.0)
        return self._unwrap(resp)
