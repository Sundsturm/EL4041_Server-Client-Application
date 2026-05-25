"""
core/api_client.py
Async REST/HTTPS client for all server communication.
Uses httpx with optional TLS verification toggle for development.
"""

import httpx
from typing import Any, Optional

from config import SERVER_BASE_URL, TLS_VERIFY
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
        await self._ensure_fresh_token()
        headers = self._auth_headers() if auth else {}
        resp = await self._client.get(path, params=params, headers=headers)
        return self._handle(resp)

    async def _post(self, path: str, body: dict = None, auth: bool = True) -> Any:
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
            # Server wraps all responses in {"status", "data", "message"}.
            # Unwrap the inner "data" dict so callers get a flat dict.
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
        Server returns envelope: { status, data: { access_token, session_token, user_id }, message }
        _handle() unwraps to the inner data dict automatically.
        """
        data = await self._post("/login", {
            "username": username,
            "password": password,
        }, auth=False)
        # Persist tokens (data is already unwrapped inner dict)
        self._auth.save_access_token(data["access_token"])
        self._auth.save_session_token(data["session_token"])
        self._auth.save_profile({
            "user_id":  data.get("user_id"),
            "username": username,   # server does not echo username, use the one we sent
        })
        return data

    async def logout(self) -> dict:
        """POST /logout"""
        try:
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

    # ─── Song / Publish endpoints ─────────────────────────────────────────────

    async def publish_song(self, metadata: dict) -> dict:
        """POST /publish — send song metadata (not the file)."""
        return await self._post("/publish", metadata)

    async def search_songs(self, query: str = "", limit: int = 50) -> dict:
        """GET /songs?q=...&limit=..."""
        return await self._get("/songs", params={"q": query, "limit": limit})

    # ─── Download / Transfer negotiation ─────────────────────────────────────

    async def request_download(self, music_id: str) -> dict:
        """
        POST /download
        Returns: { peer_id, peer_ip, peer_port, peer_token }
        """
        return await self._post("/download", {"music_id": music_id})

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
