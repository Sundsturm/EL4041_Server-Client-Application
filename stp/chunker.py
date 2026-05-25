"""
chunker.py — STP File Chunking & Reassembly
=============================================
Handles splitting a source file into fixed-size binary chunks for the sender
side, and collecting/reassembling received chunks on the receiver side.

Public API
----------
chunk_file(filepath, chunk_size)           → Iterator[(chunk_id, bytes)]
calculate_total_chunks(file_size, chunk_size) → int
class ChunkAssembler                       → collects chunks, writes output
"""

import math
import os
from typing import Iterator

from .protocol import VALID_CHUNK_SIZES, DEFAULT_CHUNK_SIZE


# ---------------------------------------------------------------------------
# Sender side: split a file into chunks
# ---------------------------------------------------------------------------

def calculate_total_chunks(file_size: int, chunk_size: int) -> int:
    """
    Calculate the total number of chunks needed to transfer a file.

    Uses ``math.ceil`` so the last (possibly smaller) chunk is counted.

    Parameters
    ----------
    file_size : int
        Total file size in bytes.
    chunk_size : int
        Chunk size in bytes.

    Returns
    -------
    int
        Total number of chunks (>= 1).

    Raises
    ------
    ValueError
        If file_size or chunk_size is <= 0.
    """
    if file_size <= 0:
        raise ValueError(f"file_size must be > 0, got {file_size}.")
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}.")
    return math.ceil(file_size / chunk_size)


def chunk_file(
    filepath: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Iterator[tuple[int, bytes]]:
    """
    Read a file and yield ``(chunk_id, chunk_bytes)`` tuples.

    The last chunk may be smaller than ``chunk_size``. Yields are 0-indexed.

    Parameters
    ----------
    filepath : str
        Path to the source audio file.
    chunk_size : int
        Number of bytes per chunk. Must be in VALID_CHUNK_SIZES.

    Yields
    ------
    tuple[int, bytes]
        ``(chunk_id, raw_bytes)``

    Raises
    ------
    FileNotFoundError
        If ``filepath`` does not exist.
    ValueError
        If ``chunk_size`` is not a valid choice.
    """
    if chunk_size not in VALID_CHUNK_SIZES:
        raise ValueError(
            f"chunk_size must be one of {VALID_CHUNK_SIZES}, got {chunk_size}."
        )
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    chunk_id = 0
    with open(filepath, "rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            yield chunk_id, data
            chunk_id += 1


# ---------------------------------------------------------------------------
# Receiver side: collect chunks and reassemble
# ---------------------------------------------------------------------------

class ChunkAssembler:
    """
    Collects incoming binary chunks in arbitrary order and writes them to
    a temporary output file in correct sequential order.

    Usage
    -----
    ::

        assembler = ChunkAssembler(
            music_id="a1b2c3",
            total_chunks=112,
            output_dir="/client/music/",
            filename="song.mp3",
        )
        assembler.add_chunk(0, data0)
        assembler.add_chunk(1, data1)
        ...
        if assembler.is_complete():
            output_path = assembler.finalize()

    Notes
    -----
    - Chunks are stored in memory as a dict until ``finalize()`` is called.
    - Duplicate chunks (same chunk_id received twice) are silently ignored.
    - ``finalize()`` writes chunks sequentially to disk in one pass.
    """

    def __init__(
        self,
        music_id: str,
        total_chunks: int,
        output_dir: str,
        filename: str,
    ) -> None:
        """
        Parameters
        ----------
        music_id : str
            Song identifier (used to name the temp file).
        total_chunks : int
            Expected total number of chunks.
        output_dir : str
            Directory where the reassembled file will be written.
        filename : str
            Original filename (e.g. ``"mysong.mp3"``).
        """
        if total_chunks < 1:
            raise ValueError(f"total_chunks must be >= 1, got {total_chunks}.")
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        self.music_id      = music_id
        self.total_chunks  = total_chunks
        self.output_dir    = output_dir
        self.filename      = filename

        # chunk_id → bytes
        self._chunks: dict[int, bytes] = {}

    # ── Chunk ingestion ────────────────────────────────────────────────────

    def add_chunk(self, chunk_id: int, data: bytes) -> None:
        """
        Store a received chunk.

        Duplicate chunk_ids are silently ignored (idempotent).

        Parameters
        ----------
        chunk_id : int
            0-indexed chunk sequence number.
        data : bytes
            Raw chunk payload bytes.

        Raises
        ------
        ValueError
            If ``chunk_id`` is out of range.
        """
        if not (0 <= chunk_id < self.total_chunks):
            raise ValueError(
                f"chunk_id {chunk_id} out of range "
                f"[0, {self.total_chunks - 1}]."
            )
        # Duplicate: ignore
        if chunk_id not in self._chunks:
            self._chunks[chunk_id] = data

    # ── Progress inspection ────────────────────────────────────────────────

    def is_complete(self) -> bool:
        """Return True if all expected chunks have been received."""
        return len(self._chunks) == self.total_chunks

    def received_count(self) -> int:
        """Return the number of chunks received so far."""
        return len(self._chunks)

    def missing_chunks(self) -> list[int]:
        """
        Return a sorted list of chunk_ids not yet received.

        Useful for generating RESUME_REQ or targeted CHUNK_NACK requests.
        """
        received = set(self._chunks.keys())
        return sorted(set(range(self.total_chunks)) - received)

    def progress_percent(self) -> float:
        """Return transfer progress as a percentage (0.0 – 100.0)."""
        if self.total_chunks == 0:
            return 100.0
        return (len(self._chunks) / self.total_chunks) * 100.0

    # ── Finalization ───────────────────────────────────────────────────────

    def finalize(self) -> str:
        """
        Write all chunks sequentially to the output file on disk.

        Must only be called when ``is_complete()`` returns ``True``.

        Returns
        -------
        str
            Absolute path to the written output file.

        Raises
        ------
        RuntimeError
            If called before all chunks have been received.
        """
        if not self.is_complete():
            missing = self.missing_chunks()
            raise RuntimeError(
                f"Cannot finalize: {len(missing)} chunk(s) still missing: "
                f"{missing[:10]}{'...' if len(missing) > 10 else ''}."
            )

        output_path = os.path.join(self.output_dir, self.filename)

        with open(output_path, "wb") as f:
            for chunk_id in range(self.total_chunks):
                f.write(self._chunks[chunk_id])

        return os.path.abspath(output_path)

    # ── Persistence helpers (resume support) ──────────────────────────────

    def get_received_chunk_ids(self) -> set[int]:
        """Return the set of chunk_ids already stored in memory."""
        return set(self._chunks.keys())

    def next_expected_chunk(self) -> int:
        """
        Return the lowest chunk_id not yet received.

        Returns ``total_chunks`` if all chunks are present.
        """
        for i in range(self.total_chunks):
            if i not in self._chunks:
                return i
        return self.total_chunks
