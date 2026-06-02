"""
core/local_storage.py
Small JSON/text persistence helpers for Termux client.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import (
    PROFILE_DIR,
    TOKENS_DIR,
    HISTORY_DIR,
    MUSIC_DIR,
    DOWNLOAD_DIR,
)


def ensure_dirs() -> None:
    for path in (PROFILE_DIR, TOKENS_DIR, HISTORY_DIR, MUSIC_DIR, DOWNLOAD_DIR):
        path.mkdir(parents=True, exist_ok=True)


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return default


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def delete_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
