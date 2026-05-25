"""
receiver.py — STP Receive-Side State Machine
==============================================
Manages the inbound side of an STP file transfer on behalf of the *requester*
(downloader).

Workflow
--------
::

    import socket
    from stp import STPReceiver, STPConfig

    sock = socket.create_connection((peer_ip, peer_port))
    sock.settimeout(30.0)
    # (TLS wrap happens here externally)

    receiver = STPReceiver(sock, peer_token="TRX_991A",
                           config=STPConfig(chunk_size=64*1024))
    receiver.initiate_transfer(
        music_id="a1b2c3d4",
        filename="song.mp3",
        mime_type="audio/mpeg",
        file_size=7340032,
        file_hash="sha256:...",
        total_chunks=112,
        output_dir="/client/music/",
    )
    output_path = receiver.receive_file()

State machine states
--------------------
IDLE → NEGOTIATING → TRANSFERRING → VERIFYING → COMPLETED | FAILED
                                    ↑ (CHUNK_NACK, up to 3 retries per chunk)
         RESUME_REQ ──────────────────────────────────────────────────────────┘

Notes
-----
- All socket I/O is **synchronous** (blocking).
- TLS wrapping is external; STPReceiver only calls sock.send/recv.
"""

import logging
import os
import socket

from .chunker import ChunkAssembler, calculate_total_chunks
from .decoder import recv_frame
from .encoder import (
    build_chunk_ack,
    build_chunk_nack,
    build_resume_req,
    build_transfer_fail,
    build_transfer_req,
    encode_frame,
)
from .integrity import verify_chunk, verify_file
from .protocol import (
    DEFAULT_CHUNK_SIZE,
    MsgType,
    STPConfig,
    STPFrame,
    Status,
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
    VERIFYING    = "VERIFYING"
    COMPLETED    = "COMPLETED"
    FAILED       = "FAILED"


# ---------------------------------------------------------------------------
# STPReceiver
# ---------------------------------------------------------------------------

class STPReceiver:
    """
    Orchestrates the inbound side of an STP file transfer.

    Parameters
    ----------
    sock : socket.socket
        A *connected* TCP socket to the owner peer.
        The caller must set ``sock.settimeout(config.timeout)`` or the
        receiver sets it during ``__init__``.
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

        # Transfer context (populated in initiate_transfer)
        self._music_id    = ""
        self._filename    = ""
        self._mime_type   = ""
        self._file_size   = 0
        self._file_hash   = ""
        self._total_chunks = 0
        self._output_dir  = ""
        self._assembler: ChunkAssembler | None = None

        # Per-chunk retry tracking {chunk_id: retry_count}
        self._chunk_retries: dict[int, int] = {}

        # Apply socket timeout
        self._sock.settimeout(self._config.timeout)

    # ── Internal send helper ───────────────────────────────────────────────

    def _send(self, frame: STPFrame) -> None:
        """Encode and send one frame on the socket."""
        data = encode_frame(frame)
        self._sock.sendall(data)

    # ── Phase 1: Initiate handshake ────────────────────────────────────────

    def initiate_transfer(
        self,
        music_id: str,
        filename: str,
        mime_type: str,
        file_size: int,
        file_hash: str,
        total_chunks: int,
        output_dir: str,
        resume_from: int = 0,
    ) -> None:
        """
        Send TRANSFER_REQ and wait for TRANSFER_ACCEPT from the owner.

        For resumed transfers, pass a non-zero ``resume_from`` — this sends
        a RESUME_REQ instead of TRANSFER_REQ.

        Parameters
        ----------
        music_id : str
            Song identifier from the server metadata.
        filename : str
            Original filename (e.g. ``"song.mp3"``).
        mime_type : str
            MIME type (e.g. ``"audio/mpeg"``).
        file_size : int
            Total file size in bytes.
        file_hash : str
            SHA-256 hex digest of the whole file (``"sha256:..."``).
        total_chunks : int
            Expected total chunks at the requested chunk_size.
        output_dir : str
            Directory where the output file will be written.
        resume_from : int
            If > 0, sends RESUME_REQ to continue an interrupted transfer.

        Raises
        ------
        ValueError
            If TRANSFER_ACCEPT is not received, or peer_token mismatch.
        ConnectionError
            On socket error.
        """
        self._state = _State.NEGOTIATING
        self._music_id    = music_id
        self._filename    = filename
        self._mime_type   = mime_type
        self._file_size   = file_size
        self._file_hash   = file_hash
        self._total_chunks = total_chunks
        self._output_dir  = output_dir

        if resume_from > 0:
            # Resume flow
            logger.info(
                "[STPReceiver] Sending RESUME_REQ for music_id=%s from chunk %d",
                music_id, resume_from,
            )
            req_frame = build_resume_req(
                peer_token=self._peer_token,
                music_id=music_id,
                resume_from=resume_from,
            )
        else:
            # Fresh transfer
            logger.info(
                "[STPReceiver] Sending TRANSFER_REQ for music_id=%s", music_id
            )
            req_frame = build_transfer_req(
                peer_token=self._peer_token,
                music_id=music_id,
                filename=filename,
                mime_type=mime_type,
                file_size=file_size,
                file_hash=file_hash,
                total_chunks=total_chunks,
                chunk_size=self._config.chunk_size,
            )

        self._send(req_frame)

        # Wait for TRANSFER_ACCEPT
        response = recv_frame(self._sock)

        if response.msg_type == MsgType.TRANSFER_FAIL:
            reason = response.metadata.get("reason", "unknown")
            self._state = _State.FAILED
            raise ValueError(f"Transfer rejected by owner: {reason}")

        if response.msg_type != MsgType.TRANSFER_ACCEPT:
            reason = (
                f"Expected TRANSFER_ACCEPT, got {response.msg_type.name}."
            )
            self._state = _State.FAILED
            raise ValueError(reason)

        accept_meta = response.metadata
        if accept_meta.get("status") != Status.OK:
            reason = accept_meta.get("reason", "Transfer not accepted.")
            self._state = _State.FAILED
            raise ValueError(f"TRANSFER_ACCEPT status not OK: {reason}")

        # Validate echoed peer_token
        if accept_meta.get("peer_token") != self._peer_token:
            self._state = _State.FAILED
            raise ValueError("peer_token mismatch in TRANSFER_ACCEPT.")

        # Update confirmed values from owner's accept
        confirmed_chunk_size = accept_meta.get("chunk_size", self._config.chunk_size)
        confirmed_total_chunks = accept_meta.get("total_chunks", total_chunks)

        logger.info(
            "[STPReceiver] TRANSFER_ACCEPT received. chunk_size=%d, total_chunks=%d",
            confirmed_chunk_size, confirmed_total_chunks,
        )

        # Initialize the chunk assembler
        self._assembler = ChunkAssembler(
            music_id=music_id,
            total_chunks=confirmed_total_chunks,
            output_dir=output_dir,
            filename=filename,
        )

        # Pre-populate already-received chunks if resuming
        if resume_from > 0 and os.path.isfile(
            os.path.join(output_dir, filename)
        ):
            # Mark chunks 0..(resume_from-1) as received from the partial file
            logger.info(
                "[STPReceiver] Resume: marking chunks 0..%d as received.",
                resume_from - 1,
            )
            self._reload_partial_chunks(
                output_dir=output_dir,
                filename=filename,
                resume_from=resume_from,
                chunk_size=confirmed_chunk_size,
            )

    def _reload_partial_chunks(
        self,
        output_dir: str,
        filename: str,
        resume_from: int,
        chunk_size: int,
    ) -> None:
        """
        Re-read the partially downloaded file and inject already-received
        chunks into the assembler so they don't need to be re-transferred.
        """
        partial_path = os.path.join(output_dir, filename)
        if not os.path.isfile(partial_path) or self._assembler is None:
            return
        with open(partial_path, "rb") as f:
            for cid in range(resume_from):
                data = f.read(chunk_size)
                if not data:
                    break
                self._assembler.add_chunk(cid, data)

    # ── Phase 2: Receive all chunks ────────────────────────────────────────

    def receive_file(self) -> str:
        """
        Loop receiving CHUNK_DATA frames until TRANSFER_END is received.

        For each chunk:
          - Verify chunk_hash (SHA-256) and hmac (HMAC-SHA256).
          - Send CHUNK_ACK on success, CHUNK_NACK on failure.
          - On CHUNK_NACK: increment retry counter; abort after max_retries.
        After TRANSFER_END:
          - Reassemble file.
          - Verify whole-file hash + HMAC.

        Returns
        -------
        str
            Absolute path to the saved output file.

        Raises
        ------
        RuntimeError
            If ``initiate_transfer()`` was not called first.
        ValueError
            On final file integrity failure.
        ConnectionError
            On socket errors.
        """
        if self._assembler is None:
            raise RuntimeError(
                "receive_file() must be called after initiate_transfer()."
            )

        self._state = _State.TRANSFERRING
        logger.info(
            "[STPReceiver] Receiving file '%s' (%d chunks)…",
            self._filename, self._total_chunks,
        )

        try:
            while True:
                frame = recv_frame(self._sock)

                # ── CHUNK_DATA ────────────────────────────────────────────
                if frame.msg_type == MsgType.CHUNK_DATA:
                    success = self._handle_chunk(frame)
                    if not success:
                        # Max retries exceeded: abort
                        fail = build_transfer_fail(
                            f"Max retries exceeded on chunk {frame.chunk_id}.",
                            music_id=self._music_id,
                        )
                        self._send(fail)
                        self._state = _State.FAILED
                        raise ValueError(
                            f"Integrity failure on chunk {frame.chunk_id} "
                            f"after {self._config.max_retries} retries."
                        )

                # ── TRANSFER_END ──────────────────────────────────────────
                elif frame.msg_type == MsgType.TRANSFER_END:
                    return self._finalize_transfer(frame)

                # ── TRANSFER_FAIL from sender ─────────────────────────────
                elif frame.msg_type == MsgType.TRANSFER_FAIL:
                    reason = frame.metadata.get("reason", "unknown")
                    logger.error(
                        "[STPReceiver] TRANSFER_FAIL from owner: %s", reason
                    )
                    self._state = _State.FAILED
                    raise ConnectionError(
                        f"Owner aborted transfer: {reason}"
                    )

                else:
                    logger.warning(
                        "[STPReceiver] Unexpected frame type %s during transfer.",
                        frame.msg_type.name,
                    )

        except (socket.error, ConnectionError, TimeoutError) as exc:
            logger.error(
                "[STPReceiver] Socket error during receive: %s", exc
            )
            self._state = _State.FAILED
            raise

    # ── Internal: handle one CHUNK_DATA frame ─────────────────────────────

    def _handle_chunk(self, frame: STPFrame) -> bool:
        """
        Verify chunk integrity and send ACK or NACK.

        Parameters
        ----------
        frame : STPFrame
            A CHUNK_DATA frame received from the owner.

        Returns
        -------
        bool
            True if chunk was accepted (ACK sent).
            False if max retries exceeded (caller should abort).
        """
        chunk_id       = frame.chunk_id
        payload        = frame.payload
        expected_hash  = frame.metadata.get("chunk_hash", "")
        expected_hmac  = frame.metadata.get("hmac", "")

        ok, reason = verify_chunk(
            payload=payload,
            expected_hash=expected_hash,
            expected_hmac=expected_hmac,
            peer_token=self._peer_token,
        )

        if ok:
            # Store chunk in assembler
            assert self._assembler is not None
            self._assembler.add_chunk(chunk_id, payload)
            self._chunk_retries.pop(chunk_id, None)

            # Send CHUNK_ACK
            ack = build_chunk_ack(music_id=self._music_id, chunk_id=chunk_id)
            self._send(ack)

            logger.debug(
                "[STPReceiver] Chunk %d/%d accepted (%.1f%%).",
                chunk_id + 1,
                self._assembler.total_chunks,
                self._assembler.progress_percent(),
            )
            return True

        else:
            # Integrity failure
            retries = self._chunk_retries.get(chunk_id, 0) + 1
            self._chunk_retries[chunk_id] = retries

            logger.warning(
                "[STPReceiver] Chunk %d integrity failure (attempt %d/%d): %s",
                chunk_id, retries, self._config.max_retries, reason,
            )

            if retries >= self._config.max_retries:
                return False  # Signal caller to abort

            # Send CHUNK_NACK requesting retransmission
            nack = build_chunk_nack(
                music_id=self._music_id,
                chunk_id=chunk_id,
                reason=reason,
            )
            self._send(nack)
            return True  # Still recovering, not yet giving up

    # ── Internal: finalize after TRANSFER_END ─────────────────────────────

    def _finalize_transfer(self, frame: STPFrame) -> str:
        """
        Reassemble the file and verify whole-file integrity.

        Parameters
        ----------
        frame : STPFrame
            The TRANSFER_END frame from the owner.

        Returns
        -------
        str
            Absolute path to the output file.

        Raises
        ------
        ValueError
            On whole-file integrity failure.
        """
        self._state = _State.VERIFYING
        assert self._assembler is not None

        expected_file_hash = frame.metadata.get("file_hash", "")
        expected_file_hmac = frame.metadata.get("hmac", "")

        logger.info("[STPReceiver] TRANSFER_END received. Reassembling file…")

        if not self._assembler.is_complete():
            missing = self._assembler.missing_chunks()
            raise ValueError(
                f"TRANSFER_END received but {len(missing)} chunks missing: "
                f"{missing[:10]}."
            )

        output_path = self._assembler.finalize()
        logger.info("[STPReceiver] File written to: %s", output_path)

        # Whole-file integrity check
        logger.info("[STPReceiver] Verifying whole-file integrity…")
        ok, reason = verify_file(
            filepath=output_path,
            expected_hash=expected_file_hash,
            expected_hmac=expected_file_hmac,
            peer_token=self._peer_token,
        )

        if not ok:
            os.remove(output_path)
            self._state = _State.FAILED
            raise ValueError(
                f"Whole-file integrity check failed: {reason}. "
                f"Partial file removed."
            )

        self._state = _State.COMPLETED
        logger.info(
            "[STPReceiver] Transfer completed successfully: %s", output_path
        )
        return output_path

    # ── Resume helper ──────────────────────────────────────────────────────

    def send_resume_request(self) -> None:
        """
        Send a RESUME_REQ to the owner indicating where to resume from.

        Determines the resume point using ``assembler.next_expected_chunk()``.
        Useful if the connection drops mid-transfer and is re-established.
        """
        if self._assembler is None:
            raise RuntimeError("No active transfer to resume.")
        resume_from = self._assembler.next_expected_chunk()
        logger.info("[STPReceiver] Sending RESUME_REQ from chunk %d.", resume_from)
        frame = build_resume_req(
            peer_token=self._peer_token,
            music_id=self._music_id,
            resume_from=resume_from,
        )
        self._send(frame)

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

    @property
    def progress(self) -> float:
        """Transfer progress as a percentage (0.0 – 100.0)."""
        if self._assembler is None:
            return 0.0
        return self._assembler.progress_percent()
