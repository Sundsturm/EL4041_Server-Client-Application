"""
decoder.py — STP Frame Deserialization
========================================
Reads raw bytes from a synchronous TCP socket and reconstructs STPFrame objects.

Reading strategy (length-prefixed framing):
  1. Read exactly HEADER_SIZE (16) bytes → parse fixed binary header.
  2. Read exactly json_len bytes          → decode UTF-8 JSON.
  3. Read exactly data_len bytes          → raw binary payload.

All socket reads use recv_exact() which loops until all requested bytes arrive
or raises ConnectionError on premature close.

Public API
----------
recv_frame(sock)   → STPFrame      (blocking, synchronous)
decode_header(data) → tuple        (internal helper, exposed for testing)
decode_metadata(data) → dict       (internal helper, exposed for testing)
"""

import json
import socket
import struct

from .protocol import (
    HEADER_FORMAT,
    HEADER_SIZE,
    STP_VERSION,
    Flags,
    MsgType,
    STPFrame,
)


# ---------------------------------------------------------------------------
# Low-level socket I/O
# ---------------------------------------------------------------------------

def recv_exact(sock: socket.socket, n: int) -> bytes:
    """
    Read exactly ``n`` bytes from ``sock``, blocking until all arrive.

    Parameters
    ----------
    sock : socket.socket
        A connected TCP socket (may be TLS-wrapped by the caller).
    n : int
        Number of bytes to read.

    Returns
    -------
    bytes
        Exactly ``n`` bytes.

    Raises
    ------
    ConnectionError
        If the connection is closed before ``n`` bytes are received.
    """
    buf = bytearray()
    while len(buf) < n:
        remaining = n - len(buf)
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError(
                f"Connection closed after {len(buf)} bytes "
                f"(expected {n})."
            )
        buf.extend(chunk)
    return bytes(buf)


# ---------------------------------------------------------------------------
# Header & metadata parsing
# ---------------------------------------------------------------------------

def decode_header(data: bytes) -> tuple[int, MsgType, int, int, int, Flags]:
    """
    Parse the 16-byte fixed binary header.

    Parameters
    ----------
    data : bytes
        Exactly HEADER_SIZE bytes.

    Returns
    -------
    tuple
        ``(version, msg_type, chunk_id, json_len, data_len, flags)``

    Raises
    ------
    ValueError
        If the header is the wrong length, the version is unsupported, or
        the msg_type is unknown.
    """
    if len(data) != HEADER_SIZE:
        raise ValueError(
            f"Header must be {HEADER_SIZE} bytes, got {len(data)}."
        )

    version, msg_type_raw, chunk_id, json_len, data_len, flags_raw = \
        struct.unpack(HEADER_FORMAT, data)

    if version != STP_VERSION:
        raise ValueError(
            f"Unsupported STP version: {version}. "
            f"Expected {STP_VERSION}."
        )

    try:
        msg_type = MsgType(msg_type_raw)
    except ValueError:
        raise ValueError(
            f"Unknown msg_type byte: 0x{msg_type_raw:02X}."
        )

    flags = Flags(flags_raw)
    return version, msg_type, chunk_id, json_len, data_len, flags


def decode_metadata(data: bytes) -> dict:
    """
    Decode the UTF-8 JSON metadata section into a Python dict.

    Parameters
    ----------
    data : bytes
        Raw UTF-8 bytes of the JSON section.

    Returns
    -------
    dict
        Parsed JSON object.

    Raises
    ------
    ValueError
        If the bytes are not valid UTF-8 JSON.
    """
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to decode JSON metadata: {exc}") from exc


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def recv_frame(sock: socket.socket) -> STPFrame:
    """
    Receive and parse one complete STP frame from a connected socket.

    Blocks until all bytes for the frame arrive.

    Frame layout read:
      1. 16-byte fixed binary header
      2. json_len bytes of UTF-8 JSON
      3. data_len bytes of binary payload

    Parameters
    ----------
    sock : socket.socket
        A connected TCP socket (plain or TLS-wrapped externally).

    Returns
    -------
    STPFrame
        The fully parsed frame.

    Raises
    ------
    ConnectionError
        On premature socket close.
    ValueError
        On protocol version mismatch, unknown msg_type, or malformed JSON.
    """
    # ── Step 1: read the fixed binary header ──────────────────────────────
    header_bytes = recv_exact(sock, HEADER_SIZE)
    version, msg_type, chunk_id, json_len, data_len, flags = \
        decode_header(header_bytes)

    # ── Step 2: read the JSON metadata section ────────────────────────────
    if json_len > 0:
        json_bytes = recv_exact(sock, json_len)
        metadata   = decode_metadata(json_bytes)
    else:
        metadata = {}

    # ── Step 3: read the binary payload ──────────────────────────────────
    if data_len > 0:
        payload = recv_exact(sock, data_len)
    else:
        payload = b""

    return STPFrame(
        version=version,
        msg_type=msg_type,
        chunk_id=chunk_id,
        flags=flags,
        metadata=metadata,
        payload=payload,
    )
