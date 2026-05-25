"""
__init__.py — STP Package Public API
======================================
Song Transfer Protocol (STP) — Data Plane for Hybrid Server-Client Music
Sharing System.

Exposes the key classes and helpers callers need to conduct a P2P transfer:

    from stp import STPSender, STPReceiver, STPConfig, STPFrame
    from stp import MsgType, Flags
    from stp import encode_frame, recv_frame
    from stp import verify_chunk, verify_file, sha256_file, hmac_sha256
    from stp import chunk_file, ChunkAssembler, calculate_total_chunks

Usage quick-start
-----------------

**Owner (sender) side:**
::

    import socket
    from stp import STPSender, STPConfig

    config = STPConfig(chunk_size=64 * 1024)   # 64 KB chunks
    with socket.create_server(("0.0.0.0", 9000)) as srv:
        conn, _ = srv.accept()
        conn.settimeout(config.timeout)
        # Wrap in TLS externally if desired, then:
        sender = STPSender(conn, peer_token="TRX_991A", config=config)
        sender.handle_transfer_req()
        sender.send_file(
            filepath="/music/song.mp3",
            music_id="a1b2c3d4",
            file_hash="sha256:...",
        )

**Requester (receiver) side:**
::

    import socket
    from stp import STPReceiver, STPConfig

    config = STPConfig(chunk_size=64 * 1024)
    sock = socket.create_connection(("192.168.1.10", 9000))
    sock.settimeout(config.timeout)
    # Wrap in TLS externally if desired, then:
    receiver = STPReceiver(sock, peer_token="TRX_991A", config=config)
    receiver.initiate_transfer(
        music_id="a1b2c3d4",
        filename="song.mp3",
        mime_type="audio/mpeg",
        file_size=7_340_032,
        file_hash="sha256:...",
        total_chunks=112,
        output_dir="/downloads/",
    )
    output_path = receiver.receive_file()
    print(f"Saved to: {output_path}")
"""

# Core data structures & constants
from .protocol import (
    STP_VERSION,
    HEADER_FORMAT,
    HEADER_SIZE,
    VALID_CHUNK_SIZES,
    DEFAULT_CHUNK_SIZE,
    MAX_RETRIES,
    DEFAULT_TIMEOUT,
    MsgType,
    Flags,
    Status,
    STPFrame,
    STPConfig,
    DEFAULT_CONFIG,
    REQUIRED_METADATA_KEYS,
)

# Serialization
from .encoder import (
    encode_frame,
    build_transfer_req,
    build_transfer_accept,
    build_chunk_data,
    build_chunk_ack,
    build_chunk_nack,
    build_resume_req,
    build_transfer_end,
    build_transfer_fail,
)

# Deserialization
from .decoder import (
    recv_frame,
    recv_exact,
    decode_header,
    decode_metadata,
)

# Integrity
from .integrity import (
    sha256_bytes,
    sha256_file,
    hmac_sha256,
    hmac_sha256_file,
    verify_chunk,
    verify_file,
)

# Chunking
from .chunker import (
    chunk_file,
    calculate_total_chunks,
    ChunkAssembler,
)

# High-level state machines
from .sender   import STPSender
from .receiver import STPReceiver


__all__ = [
    # Protocol
    "STP_VERSION",
    "HEADER_FORMAT",
    "HEADER_SIZE",
    "VALID_CHUNK_SIZES",
    "DEFAULT_CHUNK_SIZE",
    "MAX_RETRIES",
    "DEFAULT_TIMEOUT",
    "MsgType",
    "Flags",
    "Status",
    "STPFrame",
    "STPConfig",
    "DEFAULT_CONFIG",
    "REQUIRED_METADATA_KEYS",
    # Encoder
    "encode_frame",
    "build_transfer_req",
    "build_transfer_accept",
    "build_chunk_data",
    "build_chunk_ack",
    "build_chunk_nack",
    "build_resume_req",
    "build_transfer_end",
    "build_transfer_fail",
    # Decoder
    "recv_frame",
    "recv_exact",
    "decode_header",
    "decode_metadata",
    # Integrity
    "sha256_bytes",
    "sha256_file",
    "hmac_sha256",
    "hmac_sha256_file",
    "verify_chunk",
    "verify_file",
    # Chunking
    "chunk_file",
    "calculate_total_chunks",
    "ChunkAssembler",
    # State machines
    "STPSender",
    "STPReceiver",
]
