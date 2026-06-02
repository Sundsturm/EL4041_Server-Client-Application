"""
transfer/stp_sender.py
STP/TCP sender for Termux.

This matches the server STP frame format:
[16-byte binary header][JSON metadata][binary payload]
"""

from __future__ import annotations

import socket
from pathlib import Path

from config import STP_CHUNK_SIZE
from transfer.decoder import recv_frame
from transfer.encoder import (
    encode_frame,
    build_transfer_req,
    build_chunk_data,
    build_transfer_end,
    build_transfer_fail,
)
from transfer.integrity import sha256_file
from transfer.protocol import MsgType


def send_file_to_peer(
    peer_ip: str,
    peer_port: int,
    file_path: str | Path,
    music_id: str,
    peer_token: str,
    mime_type: str = "application/octet-stream",
) -> None:
    path = Path(file_path)
    size = path.stat().st_size
    total_chunks = (size + STP_CHUNK_SIZE - 1) // STP_CHUNK_SIZE
    file_hash = sha256_file(path)

    with socket.create_connection((peer_ip, peer_port), timeout=30) as conn:
        req = build_transfer_req(
            peer_token=peer_token,
            music_id=music_id,
            filename=path.name,
            mime_type=mime_type,
            file_size=size,
            file_hash=file_hash,
            total_chunks=total_chunks,
            chunk_size=STP_CHUNK_SIZE,
        )
        conn.sendall(encode_frame(req))

        # Some peers send TRANSFER_ACCEPT, some classroom implementations may not.
        # Try to read it without making the sender unusable if the receiver is minimal.
        conn.settimeout(3)
        try:
            response = recv_frame(conn)
            if response.msg_type == MsgType.TRANSFER_FAIL:
                raise RuntimeError(response.metadata.get("reason", "transfer rejected"))
        except socket.timeout:
            pass
        finally:
            conn.settimeout(30)

        with path.open("rb") as f:
            for chunk_id in range(total_chunks):
                chunk = f.read(STP_CHUNK_SIZE)
                frame = build_chunk_data(
                    music_id=music_id,
                    chunk_id=chunk_id,
                    payload=chunk,
                    peer_token=peer_token,
                    is_last=(chunk_id == total_chunks - 1),
                )
                conn.sendall(encode_frame(frame))

                # Wait for ACK/NACK.
                ack = recv_frame(conn)
                if ack.msg_type == MsgType.CHUNK_NACK:
                    # One simple retry for CLI version.
                    conn.sendall(encode_frame(frame))
                    ack = recv_frame(conn)
                if ack.msg_type != MsgType.CHUNK_ACK:
                    fail = build_transfer_fail("Expected CHUNK_ACK", music_id)
                    conn.sendall(encode_frame(fail))
                    raise RuntimeError("Transfer failed: ACK not received")

                print(f"[STP] sent chunk {chunk_id + 1}/{total_chunks}")

        end = build_transfer_end(music_id, file_hash, peer_token, str(path))
        conn.sendall(encode_frame(end))
        print("[STP] file sent successfully")
