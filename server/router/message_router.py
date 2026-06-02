"""
server/router/message_router.py
---------------------------------
Unified request dispatcher.

Both transport layers (REST and QUIC/CSP) ultimately call dispatch().
This guarantees that every msg_type has identical service behaviour
regardless of whether the request arrived over HTTPS or QUIC.

Authenticated routes require a resolved user_id (passed in by the
transport layer after verifying the JWT or session token).
"""

from __future__ import annotations

from server.models.schemas import err
from server.services import (
    auth_service,
    logging_service,
    negotiation_service,
    peer_service,
    profile_service,
    publish_service,
    session_service,
    time_service,
)

# Routes that do NOT require an authenticated user_id
UNAUTHENTICATED_ROUTES = {
    "LOGIN_REQ",
    "REGISTER_REQ",
    "TIME_SYNC_REQ",
    "SESSION_VERIFY",   # called by sender peer, no access token needed
}


async def dispatch(
    msg_type: str,
    payload: dict,
    user_id: str | None = None,
    tailscale_ip: str | None = None,
) -> dict:
    """
    Route a message to the correct service method.

    Parameters
    ----------
    msg_type:      The message type string (e.g. "LOGIN_REQ", "PUBLISH_REQ").
    payload:       Parsed request payload (dict).
    user_id:       Resolved user ID from JWT/session; None for public routes.
    tailscale_ip:  Remote Tailscale IP of the connecting client (used for
                   peer registration to store the correct 100.x.x.x address).

    Returns
    -------
    A response dict with keys: status, data, message.
    """

    # Guard: reject missing user_id for authenticated routes
    if msg_type not in UNAUTHENTICATED_ROUTES and user_id is None:
        return err("Authentication required.")

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    if msg_type == "REGISTER_REQ":
        return await auth_service.register(
            username=payload.get("username", ""),
            password=payload.get("password", ""),
            display_name=payload.get("display_name"),
        )

    if msg_type == "LOGIN_REQ":
        return await auth_service.login(
            username=payload.get("username", ""),
            password=payload.get("password", ""),
        )

    if msg_type == "LOGOUT_REQ":
        return await auth_service.logout(
            session_token=payload.get("session_token", ""),
        )

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------
    if msg_type == "REFRESH_REQ":
        return await session_service.refresh(
            session_token=payload.get("session_token", ""),
        )

    if msg_type == "SESSION_VERIFY":
        # Used by sender peers to verify an incoming peer_token before
        # accepting a STP connection.
        return await negotiation_service.verify_transfer_token(
            peer_token=payload.get("peer_token", ""),
        )

    # ------------------------------------------------------------------
    # Peer registry
    # ------------------------------------------------------------------
    if msg_type == "DISCOVERY_REQ":
        return await peer_service.discover_peers(
            music_id=payload.get("music_id", ""),
        )

    if msg_type == "PEER_STATUS_REQ":
        return await peer_service.get_peer_status(
            peer_id=payload.get("peer_id", ""),
        )

    if msg_type == "PEERS_LIST_REQ":
        return await peer_service.list_online_peers()

    if msg_type == "HEARTBEAT":
        return await peer_service.heartbeat(user_id=user_id)

    # ------------------------------------------------------------------
    # Publish / Subscribe
    # ------------------------------------------------------------------
    if msg_type == "PUBLISH_REQ":
        result = await publish_service.publish(user_id=user_id, metadata=payload)
        # Also register the peer's address in the registry
        if result["status"] == "ok" and tailscale_ip:
            stp_port = payload.get("stp_port", 0)
            await peer_service.register_peer(user_id, tailscale_ip, stp_port)
        return result

    if msg_type == "SUBSCRIBE_REQ":
        return await publish_service.search(query=payload.get("q", ""))

    if msg_type == "LIST_SONGS_REQ":
        return await publish_service.list_songs(limit=int(payload.get("limit", 100)))

    # ------------------------------------------------------------------
    # Transfer negotiation
    # ------------------------------------------------------------------
    if msg_type == "DOWNLOAD_REQ":
        return await negotiation_service.request_download(
            requester_id=user_id,
            music_id=payload.get("music_id", ""),
        )

    # ------------------------------------------------------------------
    # Logging / History
    # ------------------------------------------------------------------
    if msg_type == "LOG_REQ":
        return await logging_service.log_request(
            level=payload.get("level", "INFO"),
            source=payload.get("source", "client"),
            message=payload.get("message", ""),
        )

    if msg_type == "HISTORY_REQ":
        return await logging_service.get_history(
            user_id=user_id,
            history_type=payload.get("history_type", "download"),
        )

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------
    if msg_type == "PROFILE_GET_REQ":
        return await profile_service.get_profile(user_id=user_id)

    if msg_type == "PROFILE_UPDATE_REQ":
        return await profile_service.update_profile(
            user_id=user_id,
            display_name=payload.get("display_name", ""),
            bio=payload.get("bio", ""),
            password=payload.get("password", ""),
        )

    if msg_type == "PROFILE_DELETE_REQ":
        return await profile_service.delete_account(
            user_id=user_id,
            password=payload.get("password", ""),
        )

    # ------------------------------------------------------------------
    # Time
    # ------------------------------------------------------------------
    if msg_type == "TIME_SYNC_REQ":
        return await time_service.get_server_time()

    # ------------------------------------------------------------------
    # Unknown
    # ------------------------------------------------------------------
    return err(f"Unknown msg_type: '{msg_type}'.")
