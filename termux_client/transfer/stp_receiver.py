"""
transfer/stp_receiver.py
STP/TCP receiver for Termux.

This matches the server STP frame format:
[16-byte binary header][JSON metadata][binary payload]
"""

from __future__ import annotations

import socket
from pathlib import Path

from config import STP_LISTEN_HOST, STP_LISTEN_PORT, DOWNLOAD_DIR
from transfer.decoder import recv_frame
from transfer.encoder import build_chunk_ack, build_chunk_nack, build_transfer_fail
from transfer.integrity import sha256_bytes, sha256_file, hmac_sha256, hmac_sha256_file
from transfer.protocol import MsgType


def serve_once(
    host: str = STP_LISTEN_HOST,
    port: int = STP_LISTEN_PORT,
    accept_timeout: float = 300.0,   # 5 minutes: owner has this long to approve + connect
) -> Path | None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[STP] listening on {host}:{port} (timeout: {int(accept_timeout)}s)")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(1)
        server.settimeout(accept_timeout)

        try:
            conn, addr = server.accept()
        except socket.timeout:
            raise TimeoutError(
                f"No connection received within {int(accept_timeout)}s. "
                "Owner may not have approved in time. Please retry with 'download <id>'."
            )
        print(f"[STP] connected from {addr}")

        with conn:
            output_path: Path | None = None
            music_id = ""
            peer_token = ""
            expected_file_hash = ""

            while True:
                frame = recv_frame(conn)

                if frame.msg_type == MsgType.TRANSFER_REQ:
                    meta = frame.metadata
                    music_id = meta.get("music_id", "")
                    peer_token = meta.get("peer_token", "")
                    filename = meta.get("filename", "downloaded_song.bin")
                    expected_file_hash = meta.get("file_hash", "")

                    output_path = DOWNLOAD_DIR / filename
                    if output_path.exists():
                        output_path = DOWNLOAD_DIR / f"downloaded_{filename}"
                    output_path.write_bytes(b"")
                    print(f"[STP] receiving {filename} -> {output_path}")

                elif frame.msg_type == MsgType.CHUNK_DATA:
                    if output_path is None:
                        fail = build_transfer_fail("Received CHUNK_DATA before TRANSFER_REQ", music_id)
                        from transfer.encoder import encode_frame
                        conn.sendall(encode_frame(fail))
                        return None

                    chunk_hash = frame.metadata.get("chunk_hash", "")
                    chunk_hmac = frame.metadata.get("hmac", "")

                    if sha256_bytes(frame.payload) != chunk_hash:
                        nack = build_chunk_nack(music_id, frame.chunk_id, "chunk_hash mismatch")
                        from transfer.encoder import encode_frame
                        conn.sendall(encode_frame(nack))
                        continue

                    if peer_token and chunk_hmac and hmac_sha256(peer_token, frame.payload) != chunk_hmac:
                        nack = build_chunk_nack(music_id, frame.chunk_id, "chunk_hmac mismatch")
                        from transfer.encoder import encode_frame
                        conn.sendall(encode_frame(nack))
                        continue

                    with output_path.open("ab") as f:
                        f.write(frame.payload)

                    ack = build_chunk_ack(music_id, frame.chunk_id)
                    from transfer.encoder import encode_frame
                    conn.sendall(encode_frame(ack))
                    print(f"[STP] received chunk {frame.chunk_id}")

                elif frame.msg_type == MsgType.TRANSFER_END:
                    if output_path is None:
                        return None

                    actual_hash = sha256_file(output_path)
                    final_hash = frame.metadata.get("file_hash", expected_file_hash)
                    if final_hash and actual_hash != final_hash:
                        raise RuntimeError("File hash mismatch after transfer")

                    final_hmac = frame.metadata.get("hmac", "")
                    if peer_token and final_hmac:
                        actual_hmac = hmac_sha256_file(peer_token, output_path)
                        if actual_hmac != final_hmac:
                            raise RuntimeError("File HMAC mismatch after transfer")

                    print(f"[STP] transfer complete: {output_path}")
                    return output_path

                elif frame.msg_type == MsgType.TRANSFER_FAIL:
                    raise RuntimeError(frame.metadata.get("reason", "transfer failed"))
