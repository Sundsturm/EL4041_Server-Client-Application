"""
server/network/rest_server.py
-------------------------------
FastAPI application exposing the REST/HTTPS control plane for Desktop clients.

Transport security: TLS 1.3 via Uvicorn + Tailscale HTTPS certificate.
Authentication:     JWT Bearer token (per-session secret, verified via DB).

All endpoint handlers delegate to message_router.dispatch() so the business
logic is shared with the QUIC/CSP transport layer.
"""

from __future__ import annotations

import ssl

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server import config
from server.database import get_db
from server.models.schemas import (
    DownloadRequest,
    DeleteProfileRequest,
    HistoryRequest,
    LoginRequest,
    LogoutRequest,
    LogRequest,
    PeerStatusRequest,
    PublishRequest,
    RefreshRequest,
    RegisterRequest,
    SearchQuery,
    UpdateProfileRequest,
    VerifyTokenRequest,
    err,
)
from server.router.message_router import dispatch
from server.security import jwt_handler
from server.security.token_store import verify_session_token

app = FastAPI(title="Music Sharing Server", version="1.0.0")

# ---------------------------------------------------------------------------
# CORS (useful for any web-based tooling during development)
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# JWT dependency
# ---------------------------------------------------------------------------
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> str:
    """
    FastAPI dependency that validates the Bearer JWT and returns user_id.
    Looks up the per-session jwt_secret from the DB before verifying.
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header.")

    token = credentials.credentials

    # Decode token without verification first to extract sub (user_id)
    try:
        import jwt as pyjwt
        unverified = pyjwt.decode(token, options={"verify_signature": False})
        user_id = unverified.get("sub")
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed token.")

    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing sub claim.")

    # Find the active session secret for this user
    db = await get_db()
    async with db.execute(
        """
        SELECT jwt_secret FROM sessions
        WHERE user_id=? AND revoked=0 AND jwt_secret IS NOT NULL
        ORDER BY expires_at DESC LIMIT 1
        """,
        (user_id,),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=401, detail="No active session found.")

    payload = jwt_handler.verify_access_token(token, row["jwt_secret"])
    if payload is None:
        raise HTTPException(status_code=401, detail="Token invalid or expired.")

    return user_id


def _tailscale_ip(request: Request) -> str | None:
    """Extract the client's Tailscale IP from the incoming connection."""
    if request.client:
        ip = request.client.host
        if ip.startswith(config.TAILSCALE_IP_PREFIX):
            return ip
    return None


# ---------------------------------------------------------------------------
# Public endpoints (no auth)
# ---------------------------------------------------------------------------

@app.post("/register")
async def register(body: RegisterRequest):
    return await dispatch(
        "REGISTER_REQ",
        body.model_dump(),
    )


@app.post("/login")
async def login(body: LoginRequest):
    return await dispatch(
        "LOGIN_REQ",
        body.model_dump(),
    )


@app.get("/time")
async def server_time():
    return await dispatch("TIME_SYNC_REQ", {})


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------

@app.post("/logout")
async def logout(body: LogoutRequest, user_id: str = Depends(get_current_user)):
    return await dispatch("LOGOUT_REQ", body.model_dump(), user_id=user_id)


@app.post("/session/refresh")
async def session_refresh(body: RefreshRequest):
    return await dispatch("REFRESH_REQ", body.model_dump())


# ---------------------------------------------------------------------------
# Song catalogue endpoints
# ---------------------------------------------------------------------------

@app.post("/publish")
async def publish(
    body: PublishRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    payload = body.model_dump()
    return await dispatch(
        "PUBLISH_REQ",
        payload,
        user_id=user_id,
        tailscale_ip=_tailscale_ip(request),
    )


@app.get("/songs")
async def search_songs(q: str = "", user_id: str = Depends(get_current_user)):
    return await dispatch("SUBSCRIBE_REQ", {"q": q}, user_id=user_id)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@app.get("/profile")
async def get_profile(user_id: str = Depends(get_current_user)):
    return await dispatch("PROFILE_GET_REQ", {}, user_id=user_id)


@app.post("/profile/update")
async def update_profile(
    body: UpdateProfileRequest,
    user_id: str = Depends(get_current_user),
):
    return await dispatch("PROFILE_UPDATE_REQ", body.model_dump(), user_id=user_id)


@app.post("/profile/delete")
async def delete_profile(
    body: DeleteProfileRequest,
    user_id: str = Depends(get_current_user),
):
    return await dispatch("PROFILE_DELETE_REQ", body.model_dump(), user_id=user_id)


# ---------------------------------------------------------------------------
# Transfer negotiation
# ---------------------------------------------------------------------------

@app.post("/download")
async def download(body: DownloadRequest, user_id: str = Depends(get_current_user)):
    return await dispatch("DOWNLOAD_REQ", body.model_dump(), user_id=user_id)


@app.post("/peer/verify-token")
async def verify_peer_token_endpoint(body: VerifyTokenRequest):
    """
    Called by the sender peer to validate a peer_token before accepting STP.
    No access token required -- the peer_token itself is the credential.
    """
    return await dispatch("SESSION_VERIFY", body.model_dump())


@app.get("/peers")
async def list_peers(user_id: str = Depends(get_current_user)):
    """Return all currently online peers (used by desktop client peer tab)."""
    return await dispatch("PEERS_LIST_REQ", {}, user_id=user_id)


@app.get("/peer/status/{peer_id}")
async def peer_status(peer_id: str, user_id: str = Depends(get_current_user)):
    return await dispatch("PEER_STATUS_REQ", {"peer_id": peer_id}, user_id=user_id)


# ---------------------------------------------------------------------------
# History / Logging
# ---------------------------------------------------------------------------

@app.get("/history")
async def history(
    history_type: str = "download",
    user_id: str = Depends(get_current_user),
):
    return await dispatch(
        "HISTORY_REQ",
        {"history_type": history_type},
        user_id=user_id,
    )


@app.post("/log")
async def log_entry(body: LogRequest, user_id: str = Depends(get_current_user)):
    return await dispatch("LOG_REQ", body.model_dump(), user_id=user_id)


# ---------------------------------------------------------------------------
# Uvicorn launcher (called from main.py)
# ---------------------------------------------------------------------------

def build_uvicorn_server() -> uvicorn.Server:
    """
    Build a Uvicorn Server instance configured for TLS (Tailscale cert).
    The caller is responsible for running server.serve() in the asyncio loop.
    """
    uv_config = uvicorn.Config(
        app=app,
        host=config.REST_HOST,
        port=config.REST_PORT,
        ssl_certfile=config.CERT_PATH,
        ssl_keyfile=config.KEY_PATH,
        log_level="info",
        # h11 handles HTTP/1.1; standard Uvicorn supports HTTP/1.1 + HTTP/2
        # with uvicorn[standard] extras.
    )
    return uvicorn.Server(uv_config)
