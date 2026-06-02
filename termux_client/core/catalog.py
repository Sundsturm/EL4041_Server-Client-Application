"""
core/catalog.py
Local music catalog — maps music_id → absolute file path on this device.
Updated whenever the owner publishes a song.
Consulted when the owner approves a download request.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from config import CATALOG_FILE, MUSIC_DIR


def _load() -> dict:
    """Load the catalog from disk, returning an empty dict if missing/corrupt."""
    if not CATALOG_FILE.exists():
        return {}
    try:
        return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    CATALOG_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def register(music_id: str, file_path: str | Path) -> None:
    """Record that music_id lives at file_path on this device."""
    catalog = _load()
    catalog[music_id] = str(Path(file_path).resolve())
    _save(catalog)


def get_path(music_id: str) -> Optional[Path]:
    """
    Return the local Path for music_id, or None if not found / file missing.
    """
    catalog = _load()
    raw = catalog.get(music_id)
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_file() else None


def update_path(music_id: str, new_path: str | Path) -> None:
    """Update the catalog with a corrected path (after manual input)."""
    register(music_id, new_path)


def all_entries() -> dict[str, str]:
    """Return the full catalog dict."""
    return _load()
