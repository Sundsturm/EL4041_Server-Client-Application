"""
protocol.py — STP Core Definitions
====================================
Song Transfer Protocol (STP)
Data Plane: P2P direct transfer over TCP (synchronous sockets).

Frame Layout (wire format):
  [Fixed Binary Header: 16 bytes]
  [JSON Metadata Header: json_len bytes]
  [Binary Payload:       data_len bytes]

Fixed Binary Header format string: '!BBIIIH'
  B  = version   (1 byte,  uint8)
  B  = msg_type  (1 byte,  uint8)
  I  = chunk_id  (4 bytes, uint32 big-endian)
  I  = json_len  (4 bytes, uint32 big-endian)
  I  = data_len  (4 bytes, uint32 big-endian)
  H  = flags     (2 bytes, uint16 big-endian)
"""

import enum
import struct
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

STP_VERSION: int = 1
"""Current STP protocol version."""

HEADER_FORMAT: str = "!BBIIIH"
"""struct format string for the fixed 16-byte binary header."""

HEADER_SIZE: int = struct.calcsize(HEADER_FORMAT)
"""Byte size of the fixed binary header (must equal 16)."""

assert HEADER_SIZE == 16, f"Header size mismatch: expected 16, got {HEADER_SIZE}"

# Allowed chunk sizes (bytes)
VALID_CHUNK_SIZES: tuple[int, ...] = (
    32 * 1024,   # 32 KB
    64 * 1024,   # 64 KB
    128 * 1024,  # 128 KB
    256 * 1024,  # 256 KB
)
DEFAULT_CHUNK_SIZE: int = 64 * 1024  # 64 KB
MAX_RETRIES: int = 3
DEFAULT_TIMEOUT: float = 30.0  # seconds per socket operation


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class MsgType(enum.IntEnum):
    """STP message type codes carried in the fixed binary header."""

    TRANSFER_REQ    = 0x01  # Requester → Owner:  request to download a song
    TRANSFER_ACCEPT = 0x02  # Owner → Requester:  accept + confirm chunk params
    CHUNK_DATA      = 0x03  # Owner → Requester:  one binary chunk of the file
    CHUNK_ACK       = 0x04  # Requester → Owner:  chunk received OK
    CHUNK_NACK      = 0x05  # Requester → Owner:  chunk corrupted, resend
    RESUME_REQ      = 0x06  # Requester → Owner:  resume interrupted transfer
    TRANSFER_END    = 0x07  # Owner → Requester:  all chunks sent, file hash
    TRANSFER_FAIL   = 0x08  # Either direction:   abort with reason

    @classmethod
    def has_payload(cls, msg_type: "MsgType") -> bool:
        """Return True only for message types that carry a binary payload."""
        return msg_type == cls.CHUNK_DATA


class Flags(enum.IntFlag):
    """Bit-flag field in the fixed binary header (16 bits)."""

    NONE       = 0x0000
    ENCRYPTED  = 0x0001  # Bit 0 – reserved, payload encrypted (future)
    COMPRESSED = 0x0002  # Bit 1 – reserved, payload compressed (future)
    LAST_CHUNK = 0x0004  # Bit 2 – this is the final CHUNK_DATA frame
    RESUME     = 0x0008  # Bit 3 – this transfer is a resumed one


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class STPFrame:
    """
    Represents one complete STP protocol frame.

    Attributes
    ----------
    version : int
        Protocol version (always STP_VERSION).
    msg_type : MsgType
        The message type enum value.
    chunk_id : int
        Chunk sequence number (0-indexed). 0 for non-chunk messages.
    flags : Flags
        Bit-flags (LAST_CHUNK, RESUME, etc.).
    metadata : dict
        Decoded JSON metadata. Contents depend on msg_type.
    payload : bytes
        Raw binary payload. Empty (b"") for non-CHUNK_DATA frames.
    """

    version:  int      = STP_VERSION
    msg_type: MsgType  = MsgType.TRANSFER_REQ
    chunk_id: int      = 0
    flags:    Flags    = Flags.NONE
    metadata: dict     = field(default_factory=dict)
    payload:  bytes    = b""

    @property
    def json_len(self) -> int:
        """Byte length of the encoded JSON metadata (computed at encode time)."""
        # Computed dynamically; used internally by encoder.
        import json
        return len(json.dumps(self.metadata, separators=(",", ":")).encode("utf-8"))

    @property
    def data_len(self) -> int:
        """Byte length of the binary payload."""
        return len(self.payload)

    @property
    def is_last_chunk(self) -> bool:
        return bool(self.flags & Flags.LAST_CHUNK)

    @property
    def is_resume(self) -> bool:
        return bool(self.flags & Flags.RESUME)


@dataclass
class STPConfig:
    """
    Runtime configuration for an STP sender or receiver.

    Attributes
    ----------
    chunk_size : int
        Chunk size in bytes. Must be one of VALID_CHUNK_SIZES.
    max_retries : int
        Maximum CHUNK_NACK retransmissions per chunk before TRANSFER_FAIL.
    timeout : float
        Socket operation timeout in seconds.
    """

    chunk_size:  int   = DEFAULT_CHUNK_SIZE
    max_retries: int   = MAX_RETRIES
    timeout:     float = DEFAULT_TIMEOUT

    def __post_init__(self) -> None:
        if self.chunk_size not in VALID_CHUNK_SIZES:
            raise ValueError(
                f"Invalid chunk_size {self.chunk_size}. "
                f"Must be one of {VALID_CHUNK_SIZES}."
            )
        if self.max_retries < 1:
            raise ValueError("max_retries must be >= 1.")
        if self.timeout <= 0:
            raise ValueError("timeout must be > 0.")


# Default config instance (64 KB chunks, 3 retries, 30 s timeout)
DEFAULT_CONFIG: STPConfig = STPConfig()


# ---------------------------------------------------------------------------
# Status / reason strings used in JSON metadata
# ---------------------------------------------------------------------------

class Status:
    """String constants for the 'status' field in JSON metadata."""
    OK    = "OK"
    FAIL  = "FAIL"
    RETRY = "RETRY"


# ---------------------------------------------------------------------------
# Convenience: map msg_type → required JSON metadata keys
# ---------------------------------------------------------------------------

REQUIRED_METADATA_KEYS: dict[MsgType, list[str]] = {
    MsgType.TRANSFER_REQ:    ["peer_token", "music_id", "filename",
                               "mime_type", "file_size", "file_hash",
                               "total_chunks", "chunk_size", "timestamp"],
    MsgType.TRANSFER_ACCEPT: ["peer_token", "music_id", "total_chunks",
                               "chunk_size", "status", "timestamp"],
    MsgType.CHUNK_DATA:      ["music_id", "chunk_hash", "hmac", "timestamp"],
    MsgType.CHUNK_ACK:       ["music_id", "status", "timestamp"],
    MsgType.CHUNK_NACK:      ["music_id", "reason", "timestamp"],
    MsgType.RESUME_REQ:      ["peer_token", "music_id", "resume_from", "timestamp"],
    MsgType.TRANSFER_END:    ["music_id", "file_hash", "hmac", "timestamp"],
    MsgType.TRANSFER_FAIL:   ["reason", "timestamp"],
}
