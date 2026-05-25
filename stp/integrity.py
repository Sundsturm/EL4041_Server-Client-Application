"""
integrity.py — STP Integrity Verification
==========================================
Provides SHA-256 and HMAC-SHA256 helpers for:
  - Per-chunk integrity (chunk_hash + hmac in CHUNK_DATA)
  - Whole-file integrity (file_hash + hmac in TRANSFER_END)

Security model:
  chunk_hash  = SHA-256(payload_bytes)
      → detects accidental corruption / bit-flips
  hmac        = HMAC-SHA256(key=peer_token, msg=payload_bytes)
      → proves the chunk came from the authorized peer (tamper detection)

Both checks must pass; a single failure triggers CHUNK_NACK (or TRANSFER_FAIL
for the final file check).
"""

import hashlib
import hmac as _hmac
import os


# ---------------------------------------------------------------------------
# SHA-256 helpers
# ---------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    """
    Compute SHA-256 of raw bytes.

    Parameters
    ----------
    data : bytes
        Input data (e.g., a chunk payload).

    Returns
    -------
    str
        Lowercase hex digest, e.g. ``"sha256:abcdef..."``.
    """
    digest = hashlib.sha256(data).hexdigest()
    return f"sha256:{digest}"


def sha256_file(filepath: str, buf_size: int = 65536) -> str:
    """
    Stream-hash a file with SHA-256 without loading it fully into memory.

    Parameters
    ----------
    filepath : str
        Absolute or relative path to the file.
    buf_size : int
        Read buffer size in bytes (default 64 KB).

    Returns
    -------
    str
        Lowercase hex digest prefixed with ``"sha256:"``.

    Raises
    ------
    FileNotFoundError
        If ``filepath`` does not exist.
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(buf_size):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _strip_prefix(digest: str) -> str:
    """Strip the leading ``"sha256:"`` prefix if present."""
    return digest.removeprefix("sha256:")


# ---------------------------------------------------------------------------
# HMAC-SHA256 helpers
# ---------------------------------------------------------------------------

def hmac_sha256(key: str, data: bytes) -> str:
    """
    Compute HMAC-SHA256 using ``key`` (the peer_token) over ``data``.

    Parameters
    ----------
    key : str
        The peer_token string used as the HMAC secret key.
    data : bytes
        The message to authenticate (chunk payload or whole file bytes).

    Returns
    -------
    str
        Lowercase hex HMAC digest (no prefix).
    """
    return _hmac.new(
        key.encode("utf-8"),
        msg=data,
        digestmod=hashlib.sha256,
    ).hexdigest()


def hmac_sha256_file(key: str, filepath: str, buf_size: int = 65536) -> str:
    """
    Stream-compute HMAC-SHA256 over a file without loading it fully.

    Parameters
    ----------
    key : str
        HMAC secret key (peer_token).
    filepath : str
        Path to the file.
    buf_size : int
        Read buffer size in bytes.

    Returns
    -------
    str
        Lowercase hex HMAC digest.
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    mac = _hmac.new(key.encode("utf-8"), digestmod=hashlib.sha256)
    with open(filepath, "rb") as f:
        while chunk := f.read(buf_size):
            mac.update(chunk)
    return mac.hexdigest()


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------

def verify_chunk(
    payload: bytes,
    expected_hash: str,
    expected_hmac: str,
    peer_token: str,
) -> tuple[bool, str]:
    """
    Verify per-chunk integrity (SHA-256 + HMAC-SHA256).

    Parameters
    ----------
    payload : bytes
        Raw chunk payload bytes received.
    expected_hash : str
        ``chunk_hash`` value from the CHUNK_DATA JSON metadata.
    expected_hmac : str
        ``hmac`` value from the CHUNK_DATA JSON metadata.
    peer_token : str
        Transfer authorization token (HMAC key).

    Returns
    -------
    tuple[bool, str]
        ``(True, "")`` on success.
        ``(False, reason)`` on failure, where ``reason`` is a short string.
    """
    # 1. SHA-256 check
    actual_hash = sha256_bytes(payload)
    if not _hmac.compare_digest(_strip_prefix(actual_hash),
                                _strip_prefix(expected_hash)):
        return False, f"chunk_hash mismatch: expected {expected_hash}, got {actual_hash}"

    # 2. HMAC-SHA256 check
    actual_hmac = hmac_sha256(peer_token, payload)
    if not _hmac.compare_digest(actual_hmac, expected_hmac):
        return False, "hmac mismatch: chunk may have been tampered with"

    return True, ""


def verify_file(
    filepath: str,
    expected_hash: str,
    expected_hmac: str,
    peer_token: str,
) -> tuple[bool, str]:
    """
    Verify whole-file integrity (SHA-256 + HMAC-SHA256).

    Parameters
    ----------
    filepath : str
        Path to the fully reassembled output file.
    expected_hash : str
        ``file_hash`` from the TRANSFER_END JSON metadata.
    expected_hmac : str
        ``hmac`` from the TRANSFER_END JSON metadata.
    peer_token : str
        Transfer authorization token (HMAC key).

    Returns
    -------
    tuple[bool, str]
        ``(True, "")`` on success.
        ``(False, reason)`` on failure.
    """
    try:
        actual_hash = sha256_file(filepath)
        actual_hmac = hmac_sha256_file(peer_token, filepath)
    except FileNotFoundError as exc:
        return False, str(exc)

    if not _hmac.compare_digest(_strip_prefix(actual_hash),
                                _strip_prefix(expected_hash)):
        return False, f"file_hash mismatch: expected {expected_hash}, got {actual_hash}"

    if not _hmac.compare_digest(actual_hmac, expected_hmac):
        return False, "file hmac mismatch: file may have been tampered with"

    return True, ""
