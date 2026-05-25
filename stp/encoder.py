"""
encoder.py — STP Frame Serialization
======================================
Converts STPFrame objects into raw bytes ready to be sent over a TCP socket.

Wire format produced:
  [Fixed Binary Header: 16 bytes]  ← struct.pack('!BBIIIH', ...)
  [JSON Metadata:  json_len bytes]  ← UTF-8 encoded JSON
  [Binary Payload: data_len bytes]  ← raw audio chunk bytes (CHUNK_DATA only)

Public API
----------
encode_frame(frame)              → bytes
build_transfer_req(...)          → STPFrame
build_transfer_accept(...)       → STPFrame
build_chunk_data(...)            → STPFrame
build_chunk_ack(...)             → STPFrame
build_chunk_nack(...)            → STPFrame
build_resume_req(...)            → STPFrame
build_transfer_end(...)          → STPFrame
build_transfer_fail(...)         → STPFrame
"""

import json
import struct
import time

from .protocol import (
    STP_VERSION,
    HEADER_FORMAT,
    HEADER_SIZE,
    Flags,
    MsgType,
    STPConfig,
    STPFrame,
    Status,
    VALID_CHUNK_SIZES,
    DEFAULT_CHUNK_SIZE,
)
from .integrity import hmac_sha256, sha256_bytes


# ---------------------------------------------------------------------------
# Core serialization
# ---------------------------------------------------------------------------

def encode_frame(frame: STPFrame) -> bytes:
    """
    Serialize an STPFrame into a contiguous byte string for TCP transmission.

    Parameters
    ----------
    frame : STPFrame
        The frame to serialize.

    Returns
    -------
    bytes
        ``header_bytes + json_bytes + payload_bytes``

    Raises
    ------
    ValueError
        If the frame carries a non-empty payload for a msg_type that should
        not have one (safety check).
    """
    # Encode JSON metadata
    json_bytes: bytes = json.dumps(
        frame.metadata, separators=(",", ":")
    ).encode("utf-8")

    # Safety: only CHUNK_DATA may carry a binary payload
    if frame.payload and not MsgType.has_payload(frame.msg_type):
        raise ValueError(
            f"msg_type {frame.msg_type.name} must not carry a binary payload."
        )

    data_len: int = len(frame.payload)
    json_len:  int = len(json_bytes)

    # Pack the 16-byte fixed binary header
    header_bytes: bytes = struct.pack(
        HEADER_FORMAT,
        frame.version,          # B  uint8
        int(frame.msg_type),    # B  uint8
        frame.chunk_id,         # I  uint32
        json_len,               # I  uint32
        data_len,               # I  uint32
        int(frame.flags),       # H  uint16
    )

    assert len(header_bytes) == HEADER_SIZE

    return header_bytes + json_bytes + frame.payload


# ---------------------------------------------------------------------------
# Frame builders — one per message type
# ---------------------------------------------------------------------------

def _now() -> int:
    """Current Unix epoch timestamp as integer."""
    return int(time.time())


def build_transfer_req(
    peer_token: str,
    music_id: str,
    filename: str,
    mime_type: str,
    file_size: int,
    file_hash: str,
    total_chunks: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> STPFrame:
    """
    Build a TRANSFER_REQ frame (Requester → Owner).

    This is the opening handshake: the requester presents the server-issued
    peer_token and announces which song it wants and what chunk size it prefers.

    Parameters
    ----------
    peer_token : str
        Server-issued transfer authorization token (e.g. ``"TRX_991A"``).
    music_id : str
        Unique song identifier from the server metadata index.
    filename : str
        Original filename (e.g. ``"song.mp3"``).
    mime_type : str
        MIME type string (e.g. ``"audio/mpeg"``).
    file_size : int
        Total file size in bytes.
    file_hash : str
        SHA-256 hex digest of the complete file (prefixed ``"sha256:..."``).
    total_chunks : int
        Expected number of chunks at the proposed chunk_size.
    chunk_size : int
        Requested chunk size in bytes. Must be in VALID_CHUNK_SIZES.

    Returns
    -------
    STPFrame
    """
    if chunk_size not in VALID_CHUNK_SIZES:
        raise ValueError(
            f"chunk_size must be one of {VALID_CHUNK_SIZES}, got {chunk_size}."
        )

    metadata = {
        "peer_token":   peer_token,
        "music_id":     music_id,
        "filename":     filename,
        "mime_type":    mime_type,
        "file_size":    file_size,
        "file_hash":    file_hash,
        "total_chunks": total_chunks,
        "chunk_size":   chunk_size,
        "timestamp":    _now(),
    }
    return STPFrame(
        version=STP_VERSION,
        msg_type=MsgType.TRANSFER_REQ,
        chunk_id=0,
        flags=Flags.NONE,
        metadata=metadata,
        payload=b"",
    )


def build_transfer_accept(
    peer_token: str,
    music_id: str,
    total_chunks: int,
    chunk_size: int,
    is_resume: bool = False,
) -> STPFrame:
    """
    Build a TRANSFER_ACCEPT frame (Owner → Requester).

    Confirms the transfer parameters. The owner echoes back the confirmed
    chunk_size (may differ from what requester asked if clamped).

    Parameters
    ----------
    peer_token : str
        Echo of the peer_token for cross-validation.
    music_id : str
        Song identifier.
    total_chunks : int
        Total chunks at the confirmed chunk_size.
    chunk_size : int
        Confirmed chunk size in bytes.
    is_resume : bool
        True if this accept is for a RESUME_REQ.

    Returns
    -------
    STPFrame
    """
    flags = Flags.RESUME if is_resume else Flags.NONE
    metadata = {
        "peer_token":   peer_token,
        "music_id":     music_id,
        "total_chunks": total_chunks,
        "chunk_size":   chunk_size,
        "status":       Status.OK,
        "timestamp":    _now(),
    }
    return STPFrame(
        version=STP_VERSION,
        msg_type=MsgType.TRANSFER_ACCEPT,
        chunk_id=0,
        flags=flags,
        metadata=metadata,
        payload=b"",
    )


def build_chunk_data(
    music_id: str,
    chunk_id: int,
    payload: bytes,
    peer_token: str,
    is_last: bool = False,
) -> STPFrame:
    """
    Build a CHUNK_DATA frame (Owner → Requester).

    Computes chunk_hash (SHA-256) and hmac (HMAC-SHA256 keyed on peer_token)
    over the raw payload bytes and embeds both in the JSON metadata.

    Parameters
    ----------
    music_id : str
        Song identifier.
    chunk_id : int
        0-indexed chunk sequence number.
    payload : bytes
        Raw file bytes for this chunk.
    peer_token : str
        HMAC key.
    is_last : bool
        Set True on the final chunk; sets the LAST_CHUNK flag.

    Returns
    -------
    STPFrame
    """
    chunk_hash = sha256_bytes(payload)
    hmac_val   = hmac_sha256(peer_token, payload)

    flags    = Flags.LAST_CHUNK if is_last else Flags.NONE
    metadata = {
        "music_id":   music_id,
        "chunk_hash": chunk_hash,
        "hmac":       hmac_val,
        "timestamp":  _now(),
    }
    return STPFrame(
        version=STP_VERSION,
        msg_type=MsgType.CHUNK_DATA,
        chunk_id=chunk_id,
        flags=flags,
        metadata=metadata,
        payload=payload,
    )


def build_chunk_ack(music_id: str, chunk_id: int) -> STPFrame:
    """
    Build a CHUNK_ACK frame (Requester → Owner).

    Acknowledges successful receipt and integrity verification of chunk_id.

    Parameters
    ----------
    music_id : str
        Song identifier.
    chunk_id : int
        The chunk that was successfully received.

    Returns
    -------
    STPFrame
    """
    metadata = {
        "music_id":  music_id,
        "status":    Status.OK,
        "timestamp": _now(),
    }
    return STPFrame(
        version=STP_VERSION,
        msg_type=MsgType.CHUNK_ACK,
        chunk_id=chunk_id,
        flags=Flags.NONE,
        metadata=metadata,
        payload=b"",
    )


def build_chunk_nack(music_id: str, chunk_id: int, reason: str) -> STPFrame:
    """
    Build a CHUNK_NACK frame (Requester → Owner).

    Requests retransmission of chunk_id due to a failed integrity check.

    Parameters
    ----------
    music_id : str
        Song identifier.
    chunk_id : int
        The failed chunk that should be retransmitted.
    reason : str
        Human-readable failure reason (e.g. ``"chunk_hash mismatch"``).

    Returns
    -------
    STPFrame
    """
    metadata = {
        "music_id":  music_id,
        "reason":    reason,
        "timestamp": _now(),
    }
    return STPFrame(
        version=STP_VERSION,
        msg_type=MsgType.CHUNK_NACK,
        chunk_id=chunk_id,
        flags=Flags.NONE,
        metadata=metadata,
        payload=b"",
    )


def build_resume_req(
    peer_token: str,
    music_id: str,
    resume_from: int,
) -> STPFrame:
    """
    Build a RESUME_REQ frame (Requester → Owner).

    Sent after a connection interruption to resume from a specific chunk_id.

    Parameters
    ----------
    peer_token : str
        Server-issued peer token for re-authentication.
    music_id : str
        Song identifier.
    resume_from : int
        The chunk_id to resume from (first missing/corrupted chunk).

    Returns
    -------
    STPFrame
    """
    metadata = {
        "peer_token":  peer_token,
        "music_id":    music_id,
        "resume_from": resume_from,
        "timestamp":   _now(),
    }
    return STPFrame(
        version=STP_VERSION,
        msg_type=MsgType.RESUME_REQ,
        chunk_id=0,
        flags=Flags.RESUME,
        metadata=metadata,
        payload=b"",
    )


def build_transfer_end(
    music_id: str,
    file_hash: str,
    peer_token: str,
    filepath: str,
) -> STPFrame:
    """
    Build a TRANSFER_END frame (Owner → Requester).

    Signals that all chunks have been sent. Includes the whole-file SHA-256
    hash and an HMAC-SHA256 over the entire file for final verification.

    Parameters
    ----------
    music_id : str
        Song identifier.
    file_hash : str
        SHA-256 hex digest of the whole file (``"sha256:..."``).
    peer_token : str
        HMAC key for the whole-file HMAC.
    filepath : str
        Local path to the source file (needed to compute the file HMAC).

    Returns
    -------
    STPFrame
    """
    from .integrity import hmac_sha256_file
    hmac_val = hmac_sha256_file(peer_token, filepath)

    metadata = {
        "music_id":  music_id,
        "file_hash": file_hash,
        "hmac":      hmac_val,
        "timestamp": _now(),
    }
    return STPFrame(
        version=STP_VERSION,
        msg_type=MsgType.TRANSFER_END,
        chunk_id=0,
        flags=Flags.NONE,
        metadata=metadata,
        payload=b"",
    )


def build_transfer_fail(reason: str, music_id: str = "") -> STPFrame:
    """
    Build a TRANSFER_FAIL frame (either direction).

    Aborts the ongoing transfer.

    Parameters
    ----------
    reason : str
        Human-readable reason for the failure.
    music_id : str
        Optional song identifier for context.

    Returns
    -------
    STPFrame
    """
    metadata = {
        "reason":    reason,
        "timestamp": _now(),
    }
    if music_id:
        metadata["music_id"] = music_id

    return STPFrame(
        version=STP_VERSION,
        msg_type=MsgType.TRANSFER_FAIL,
        chunk_id=0,
        flags=Flags.NONE,
        metadata=metadata,
        payload=b"",
    )
