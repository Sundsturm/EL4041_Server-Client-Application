"""
server/models/schemas.py
-------------------------
Pydantic v2 request/response models for the REST API,
plus lightweight dataclasses for the CSP/QUIC layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


# ===========================================================================
# Shared API response wrapper
# ===========================================================================

class APIResponse(BaseModel, Generic[T]):
    """Uniform envelope returned by every REST endpoint."""
    status: str                  # "ok" or "error"
    data: T | None = None
    message: str = ""


def ok(data: Any = None, message: str = "") -> dict:
    """Shorthand to build a success response dict."""
    return {"status": "ok", "data": data, "message": message}


def err(message: str, data: Any = None) -> dict:
    """Shorthand to build an error response dict."""
    return {"status": "error", "data": data, "message": message}


# ===========================================================================
# Authentication
# ===========================================================================

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6)
    display_name: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class LogoutRequest(BaseModel):
    session_token: str


class RefreshRequest(BaseModel):
    session_token: str


# ===========================================================================
# Publish / Search
# ===========================================================================

class PublishRequest(BaseModel):
    filename: str
    mime_type: str
    size: int = Field(..., gt=0)
    hmac_hash: str            # HMAC-SHA256 of the file, computed client-side
    stp_port: int = Field(..., gt=0, lt=65536)   # port where sender listens for STP
    title:  str = ""
    artist: str = ""
    album:  str = ""


class SearchQuery(BaseModel):
    q: str = Field(..., min_length=1)


# ===========================================================================
# Download / Transfer Negotiation
# ===========================================================================

class DownloadRequest(BaseModel):
    music_id: str


class VerifyTokenRequest(BaseModel):
    peer_token: str


# ===========================================================================
# Peer
# ===========================================================================

class PeerStatusRequest(BaseModel):
    peer_id: str


# ===========================================================================
# Profile
# ===========================================================================

class UpdateProfileRequest(BaseModel):
    display_name: str = ""
    bio:          str = ""
    password:     str = ""   # optional; empty means no change


class DeleteProfileRequest(BaseModel):
    password: str


# ===========================================================================
# History / Logging
# ===========================================================================

class HistoryRequest(BaseModel):
    history_type: str = "download"   # "download" | "publish" | "login"


class LogRequest(BaseModel):
    level: str = "INFO"
    source: str
    message: str


# ===========================================================================
# CSP (Custom Socket Protocol) dataclasses used by the QUIC layer
# ===========================================================================

@dataclass
class CSPMessage:
    """A single decoded message received over a QUIC stream."""
    msg_type: str
    payload: dict = field(default_factory=dict)


@dataclass
class CSPResponse:
    """Response envelope sent back over a QUIC stream."""
    status: str                   # "ok" or "error"
    data: dict = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "data": self.data,
            "message": self.message,
        }
