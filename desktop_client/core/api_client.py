"""
core/api_client.py
Async REST/HTTPS client for all server communication.
Uses httpx with optional TLS verification toggle for development.
"""

import httpx
from typing import Any, Optional

from config import SERVER_BASE_URL, TLS_VERIFY, STP_LISTEN_PORT
from core.auth_manager import AuthManager


class APIError(Exception):
    """Raised when server returns an error response."""
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class APIClient:
    """
    Wraps every REST endpoint on the server.

    All public methods are async. Call them from a QThread or
    asyncio bridge (see transfer_manager.py).
    """

    def __init__(self, auth: AuthManager):
        self._auth = auth
        self._base = SERVER_BASE_URL.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    # ─── httpx lifecycle ─────────────────────────────────────────────────────

    async def start(self):
        self._client = httpx.AsyncClient(
            base_url=self._base,
            verify=TLS_VERIFY,
            timeout=30.0,
        )

    async def stop(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ─── Internal helpers ────────────────────────────────────────────────────

    def _auth_headers(self) -> dict:
        token = self._auth.get_access_token()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    async def _ensure_fresh_token(self):
        """Transparently refresh access token if near expiry."""
        if self._auth.is_access_token_expired():
            session = self._auth.get_session_token()
            if session:
                try:
                    await self.refresh_session(session)
                except APIError:
                    pass  # Caller will receive 401 and handle it

    async def _get(self, path: str, params: dict = None, auth: bool = True) -> Any:
        if auth:
            await self._ensure_fresh_token()
        headers = self._auth_headers() if auth else {}
        resp = await self._client.get(path, params=params, headers=headers)
        return self._handle(resp)

    async def _post(self, path: str, body: dict = None, auth: bool = True) -> Any:
        if auth:
            await self._ensure_fresh_token()
        headers = self._auth_headers() if auth else {}
        resp = await self._client.post(path, json=body or {}, headers=headers)
        return self._handle(resp)

    @staticmethod
    def _handle(resp: httpx.Response) -> Any:
        try:
            body = resp.json()
        except Exception:
            body = {"detail": resp.text}

        if resp.is_success:
            if isinstance(body, dict) and "status" in body:
                if body.get("status") == "error":
                    raise APIError(body.get("message") or str(body), resp.status_code)
                return body.get("data") or {}
            return body

        msg = body.get("detail") or body.get("message") or str(body)
        raise APIError(msg, resp.status_code)

    # ─── Auth endpoints ───────────────────────────────────────────────────────

    async def register(self, username: str, password: str) -> dict:
        """POST /register"""
        return await self._post("/register", {
            "username": username,
            "password": password,
        }, auth=False)

    async def login(self, username: str, password: str) -> dict:
        """
        POST /login
        Server returns envelope:
        { status, data: { access_token, session_token, user_id }, message }
        """
        data = await self._post("/login", {
            "username": username,
            "password": password,
        }, auth=False)

        print("LOGIN RESPONSE:", data)

        self._auth.save_access_token(data["access_token"])
        self._auth.save_session_token(data["session_token"])
        self._auth.save_profile({
            "user_id": data.get("user_id"),
            "username": username,
        })
        return data

    async def logout(self) -> dict:
        """POST /logout"""
        try:
            # Most servers expect the session token in the body for logout.
            session = self._auth.get_session_token()
            if session:
                result = await self._post("/logout", {"session_token": session})
            else:
                result = await self._post("/logout")
        finally:
            self._auth.logout()
        return result

    async def refresh_session(self, session_token: str) -> dict:
        """
        POST /session/refresh
        Exchanges session token for a new access token.
        """
        data = await self._post("/session/refresh", {
            "session_token": session_token,
        }, auth=False)
        self._auth.save_access_token(data["access_token"])
        return data

    # ─── Profile endpoints ───────────────────────────────────────────────────

    async def get_profile(self) -> dict:
        """GET /profile"""
        return await self._get("/profile")

    async def update_profile(
        self,
        username: str = "",
        bio: str = "",
        password: str = "",
    ) -> dict:
        """POST /profile/update"""
        body: dict = {}
        if username:
            body["username"] = username
        if bio is not None:
            body["bio"] = bio
        if password:
            body["password"] = password
        return await self._post("/profile/update", body)

    async def delete_profile(self, password: str) -> dict:
        """POST /profile/delete"""
        try:
            return await self._post(
                "/profile/delete",
                {"password": password},
            )
        finally:
            self._auth.logout()

    # ─── Song / Publish endpoints ────────────────────────────────────────────

    async def publish_song(self, metadata: dict) -> dict:
        """POST /publish — send song metadata, not the file."""
        return await self._post("/publish", metadata)

    async def search_songs(self, query: str = "", limit: int = 50) -> dict:
        """GET /songs?q=...&limit=..."""
        return await self._get("/songs", params={"q": query, "limit": limit})

    # ─── Transfer request / approval flow ────────────────────────────────────

    async def request_download(
        self,
        music_id: str,
        requester_port: int = STP_LISTEN_PORT,
    ) -> dict:
        """
        POST /download

        New approval-based flow:
        returns { request_id, song_title, status }.
        """
        return await self._post("/download", {
            "music_id": music_id,
            "requester_port": requester_port,
        })

    async def get_pending_requests(self, timeout: int = 28) -> dict:
        """
        GET /transfer/requests?timeout=28

        Long-poll endpoint for owners. The per-request timeout must be larger
        than the server long-poll window.
        """
        await self._ensure_fresh_token()
        headers = self._auth_headers()
        request_timeout = httpx.Timeout(timeout + 5.0, connect=10.0)
        resp = await self._client.get(
            "/transfer/requests",
            params={"timeout": timeout},
            headers=headers,
            timeout=request_timeout,
        )
        return self._handle(resp)

    async def approve_transfer(self, request_id: str) -> dict:
        """
        POST /transfer/approve

        Owner approves a pending request.
        Returns { requester_ip, requester_port, peer_token, music_id, filename, ... }.
        """
        return await self._post("/transfer/approve", {"request_id": request_id})

    async def reject_transfer(self, request_id: str, reason: str = "") -> dict:
        """POST /transfer/reject"""
        return await self._post("/transfer/reject", {
            "request_id": request_id,
            "reason": reason,
        })

    async def get_transfer_status(self, request_id: str) -> dict:
        """GET /transfer/status/{request_id}"""
        return await self._get(f"/transfer/status/{request_id}")

    async def get_my_downloads(self) -> dict:
        """GET /transfer/my-downloads"""
        return await self._get("/transfer/my-downloads")

    async def update_transfer_status(self, request_id: str, status: str) -> dict:
        """POST /transfer/update-status — status: in_progress|completed|failed"""
        return await self._post("/transfer/update-status", {
            "request_id": request_id,
            "status": status,
        })

    async def verify_peer_token(self, peer_token: str) -> dict:
        """
        POST /peer/verify-token

        Kept for backward compatibility with the older direct STP flow.
        """
        return await self._post(
            "/peer/verify-token",
            {"peer_token": peer_token},
            auth=False,
        )

    # ─── History ─────────────────────────────────────────────────────────────

    async def get_history(self) -> dict:
        """GET /history"""
        return await self._get("/history")

    # ─── Peer status ─────────────────────────────────────────────────────────

    async def get_peers(self) -> dict:
        """GET /peers — list all online peers from the server registry."""
        return await self._get("/peers")

    async def get_peer_status(self, peer_id: str) -> dict:
        """GET /peer/status/{peer_id}"""
        return await self._get(f"/peer/status/{peer_id}")
