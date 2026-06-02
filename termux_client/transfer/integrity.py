"""
transfer/integrity.py
SHA256 and HMAC-SHA256 helpers for STP transfer integrity.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path, block_size: int = 65536) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            h.update(block)
    return h.hexdigest()


def hmac_sha256(key: str, data: bytes) -> str:
    return hmac.new(key.encode("utf-8"), data, hashlib.sha256).hexdigest()


def hmac_sha256_file(key: str, path: str | Path, block_size: int = 65536) -> str:
    h = hmac.new(key.encode("utf-8"), digestmod=hashlib.sha256)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            h.update(block)
    return h.hexdigest()
