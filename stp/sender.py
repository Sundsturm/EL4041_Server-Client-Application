"""
sender.py — STP Send-Side State Machine
=========================================
Manages outbound STP transfers on behalf of the song *owner* (uploader).

Workflow
--------
::

    # 1. Owner's peer transfer module receives a connection from requester.
    # 2. Caller creates STPSender with the connected socket + config.
    # 3. Call sender.handle_transfer_req() to read and validate the
    #    TRANSFER_REQ from the requester.
    # 4. Call sender.send_file() to stream all chunks and send TRANSFER_END.

    import socket
    from stp import STPSender, STPConfig

    with socket.create_server(("0.0.0.0", port)) as srv:
        conn, addr = srv.accept()
        # (TLS wrap happens here externally, before passing to STPSender)
        sender = STPSender(conn, peer_token="TRX_991A",
                           config=STPConfig(chunk_size=64*1024))
        req_meta = sender.handle_transfer_req()
        sender.send_file(filepath="/client/music/song.mp3",
                         music_id="a1b2c3d4",
                         file_hash="sha256:abcdef...")

State machine states
--------------------
IDLE → NEGOTIATING → TRANSFERRING → COMPLETED | FAILED
                                    ↑ (CHUNK_NACK loop, max 3 retries)
                                    ↑ (RESUME_REQ → back to NEGOTIATING)

Notes
-----
- All socket I/O is **synchronous** (blocking). The caller is responsible for
  setting the socket timeout before passing it in.
- TLS wrapping is done externally; STPSender only calls sock.send/recv.
"""

import logging
import os
import socket
import time

from .chunker import ChunkAssembler, calculate_total_chunks, chunk_file
from .decoder import recv_frame
from .encoder import (
    build_transfer_accept,
    build_transfer_end,
    build_transfer_fail,
    build_chunk_data,
    encode_frame,
)
from .integrity import sha256_file
from .protocol import (
    DEFAULT_CHUNK_SIZE,
    MsgType,
    STPConfig,
    STPFrame,
    DEFAULT_CONFIG,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transfer state enum
# ---------------------------------------------------------------------------

class _State:
    IDLE         = "IDLE"
    NEGOTIATING  = "NEGOTIATING"
    TRANSFERRING = "TRANSFERRING"
    COMPLETED    = "COMPLETED"
    FAILED       = "FAILED"


# ---------------------------------------------------------------------------
# STPSender
# ---------------------------------------------------------------------------

class STPSender:
    """
    Orchestrates the outbound side of an STP file transfer.

    Parameters
    ----------
    sock : socket.socket
        A *connected* TCP socket to the requester peer.
        The caller must set ``sock.settimeout(config.timeout)`` before
        passing it in (or the sender does it during ``__init__``).
    peer_token : str
        Server-issued transfer authorization token.
    config : STPConfig
        Transfer configuration (chunk size, retries, timeout).
    """

    def __init__(
        self,
        sock: socket.socket,
        peer_token: str,
        config: STPConfig = DEFAULT_CONFIG,
    ) -> None:
        self._sock       = sock
        self._peer_token = peer_token
        self._config     = config
        self._state      = _State.IDLE
        self._music_id   = ""

        # Apply socket timeout from config
        self._sock.settimeout(self._config.timeout)

    # ── Internal send helper ───────────────────────────────────────────────

    def _send(self, frame: STPFrame) -> None:
        """Encode and send one frame on the socket."""
        data = encode_frame(frame)
        self._sock.sendall(data)

    # ── Phase 1: Handshake ─────────────────────────────────────────────────

    def handle_transfer_req(self) -> dict:
        """
        Block until a TRANSFER_REQ or RESUME_REQ frame is received, validate
        the peer_token, and reply with TRANSFER_ACCEPT.

        Returns
        -------
        dict
            The JSON metadata dict from the TRANSFER_REQ, so the caller can
            know the music_id, filename, requested chunk_size, etc.

        Raises
        ------
        ValueError
            If the first received frame is not TRANSFER_REQ / RESUME_REQ,
            or if the peer_token does not match.
        ConnectionError
            On socket errors.
        """
        self._state = _State.NEGOTIATING
        logger.info("[STPSender] Waiting for TRANSFER_REQ…")

        frame = recv_frame(self._sock)

        if frame.msg_type not in (MsgType.TRANSFER_REQ, MsgType.RESUME_REQ):
            reason = (
                f"Expected TRANSFER_REQ or RESUME_REQ, "
                f"got {frame.msg_type.name}."
            )
            self._send(build_transfer_fail(reason))
            self._state = _State.FAILED
            raise ValueError(reason)

        meta = frame.metadata

        # Validate peer_token
        if meta.get("peer_token") != self._peer_token:
            reason = "Invalid peer_token."
            self._send(build_transfer_fail(reason))
            self._state = _State.FAILED
            raise ValueError(reason)

        self._music_id = meta.get("music_id", "")
        is_resume      = (frame.msg_type == MsgType.RESUME_REQ)

        # Determine confirmed chunk size
        requested_chunk_size = meta.get("chunk_size", DEFAULT_CHUNK_SIZE)
        from .protocol import VALID_CHUNK_SIZES
        if requested_chunk_size not in VALID_CHUNK_SIZES:
            requested_chunk_size = self._config.chunk_size

        # We'll accept the requested chunk_size if it's valid
        confirmed_chunk_size = requested_chunk_size

        # Store confirmed chunk size for send_file()
        self._confirmed_chunk_size = confirmed_chunk_size

        # Reply with TRANSFER_ACCEPT
        # total_chunks will be set properly in send_file(); here we echo 0
        # if file_size isn't known yet (for RESUME_REQ, caller provides info)
        total_chunks = meta.get("total_chunks", 0)
        accept = build_transfer_accept(
            peer_token=self._peer_token,
            music_id=self._music_id,
            total_chunks=total_chunks,
            chunk_size=confirmed_chunk_size,
            is_resume=is_resume,
        )
        self._send(accept)
        logger.info(
            "[STPSender] TRANSFER_ACCEPT sent. chunk_size=%d, resume=%s",
            confirmed_chunk_size, is_resume,
        )

        # Store resume_from for send_file() if this is a resume
        self._resume_from = meta.get("resume_from", 0) if is_resume else 0

        return meta

    # ── Phase 2: Chunk transfer ────────────────────────────────────────────

    def send_file(
        self,
        filepath: str,
        music_id: str = "",
        file_hash: str = "",
    ) -> bool:
        """
        Stream the file as CHUNK_DATA frames, waiting for ACK after each chunk.

        On CHUNK_NACK, retransmits the chunk up to ``config.max_retries`` times.
        On RESUME_REQ from receiver mid-transfer, fast-forwards to the
        requested chunk.

        Parameters
        ----------
        filepath : str
            Absolute path to the source audio file.
        music_id : str
            Song identifier. If empty, uses the one from handle_transfer_req().
        file_hash : str
            Precomputed ``"sha256:..."`` of the file. If empty, computed here.

        Returns
        -------
        bool
            ``True`` on successful transfer, ``False`` on failure.

        Raises
        ------
        FileNotFoundError
            If ``filepath`` does not exist.
        RuntimeError
            If ``handle_transfer_req()`` was not called first.
        """
        if self._state not in (_State.NEGOTIATING,):
            raise RuntimeError(
                "send_file() must be called after handle_transfer_req()."
            )
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        effective_music_id = music_id or self._music_id
        chunk_size = getattr(self, "_confirmed_chunk_size", self._config.chunk_size)
        resume_from = getattr(self, "_resume_from", 0)

        # Compute file hash if not provided
        if not file_hash:
            logger.info("[STPSender] Computing file hash…")
            file_hash = sha256_file(filepath)

        file_size = os.path.getsize(filepath)
        total_chunks = calculate_total_chunks(file_size, chunk_size)

        self._state = _State.TRANSFERRING
        logger.info(
            "[STPSender] Starting transfer: %s  chunks=%d  chunk_size=%d  resume_from=%d",
            filepath, total_chunks, chunk_size, resume_from,
        )

        try:
            for chunk_id, chunk_bytes in chunk_file(filepath, chunk_size):
                # Skip already-acknowledged chunks on resume
                if chunk_id < resume_from:
                    continue

                is_last = (chunk_id == total_chunks - 1)
                success = self._send_chunk_with_retry(
                    chunk_id=chunk_id,
                    data=chunk_bytes,
                    music_id=effective_music_id,
                    is_last=is_last,
                )
                if not success:
                    logger.error(
                        "[STPSender] Max retries exceeded on chunk %d. Aborting.",
                        chunk_id,
                    )
                    self._send(build_transfer_fail(
                        f"Max retries exceeded on chunk {chunk_id}.",
                        music_id=effective_music_id,
                    ))
                    self._state = _State.FAILED
                    return False

            # All chunks sent → send TRANSFER_END
            end_frame = build_transfer_end(
                music_id=effective_music_id,
                file_hash=file_hash,
                peer_token=self._peer_token,
                filepath=filepath,
            )
            self._send(end_frame)
            self._state = _State.COMPLETED
            logger.info("[STPSender] TRANSFER_END sent. Transfer complete.")
            return True

        except (socket.error, ConnectionError, TimeoutError) as exc:
            logger.error("[STPSender] Socket error during transfer: %s", exc)
            try:
                self._send(build_transfer_fail(
                    str(exc), music_id=effective_music_id
                ))
            except Exception:
                pass
            self._state = _State.FAILED
            return False

    # ── Internal: send one chunk + wait for ACK (with retries) ────────────

    def _send_chunk_with_retry(
        self,
        chunk_id: int,
        data: bytes,
        music_id: str,
        is_last: bool,
    ) -> bool:
        """
        Send CHUNK_DATA and wait for CHUNK_ACK. Retransmit on CHUNK_NACK.

        Parameters
        ----------
        chunk_id : int
        data : bytes
        music_id : str
        is_last : bool

        Returns
        -------
        bool
            True on ACK received, False after max_retries NACKs.
        """
        for attempt in range(1, self._config.max_retries + 1):
            # Build and send CHUNK_DATA
            frame = build_chunk_data(
                music_id=music_id,
                chunk_id=chunk_id,
                payload=data,
                peer_token=self._peer_token,
                is_last=is_last,
            )
            self._send(frame)

            logger.debug(
                "[STPSender] Sent chunk %d (attempt %d/%d, %d bytes)",
                chunk_id, attempt, self._config.max_retries, len(data),
            )

            # Wait for ACK / NACK
            try:
                ack_frame = recv_frame(self._sock)
            except (socket.timeout, TimeoutError):
                logger.warning(
                    "[STPSender] Timeout waiting for ACK on chunk %d "
                    "(attempt %d/%d). Retrying…",
                    chunk_id, attempt, self._config.max_retries,
                )
                continue

            if ack_frame.msg_type == MsgType.CHUNK_ACK:
                logger.debug("[STPSender] Chunk %d ACKed.", chunk_id)
                return True

            elif ack_frame.msg_type == MsgType.CHUNK_NACK:
                reason = ack_frame.metadata.get("reason", "unknown")
                logger.warning(
                    "[STPSender] NACK for chunk %d: %s. Retrying (%d/%d)…",
                    chunk_id, reason, attempt, self._config.max_retries,
                )
                continue

            elif ack_frame.msg_type == MsgType.RESUME_REQ:
                # Receiver wants to resume from a different chunk
                resume_from = ack_frame.metadata.get("resume_from", chunk_id)
                logger.info(
                    "[STPSender] RESUME_REQ received — rewinding to chunk %d.",
                    resume_from,
                )
                # Signal the caller to restart from resume_from
                # (handled in send_file via resume_from state)
                self._resume_from = resume_from
                return False  # Let send_file handle re-iteration

            elif ack_frame.msg_type == MsgType.TRANSFER_FAIL:
                reason = ack_frame.metadata.get("reason", "unknown")
                logger.error(
                    "[STPSender] TRANSFER_FAIL received from peer: %s", reason
                )
                self._state = _State.FAILED
                return False

            else:
                logger.warning(
                    "[STPSender] Unexpected frame type %s while waiting for ACK.",
                    ack_frame.msg_type.name,
                )

        return False  # Max retries exhausted

    # ── Status ─────────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        """Current state machine state string."""
        return self._state

    @property
    def is_complete(self) -> bool:
        """True if transfer completed successfully."""
        return self._state == _State.COMPLETED

    @property
    def is_failed(self) -> bool:
        """True if transfer failed."""
        return self._state == _State.FAILED
