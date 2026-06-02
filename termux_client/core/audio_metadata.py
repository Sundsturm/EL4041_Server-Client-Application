"""
core/audio_metadata.py
Extract local audio metadata for publishing.
"""

from __future__ import annotations

from pathlib import Path
from mutagen import File as MutagenFile


def extract_metadata(path: str | Path) -> dict:
    try:
        audio = MutagenFile(str(path), easy=True)
        if audio is None:
            return {"title": "", "artist": "", "album": "", "duration": 0}

        result = {
            "title": audio.get("title", [""])[0],
            "artist": audio.get("artist", [""])[0],
            "album": audio.get("album", [""])[0],
            "duration": 0,
        }
        try:
            result["duration"] = int(audio.info.length)
        except Exception:
            pass
        return result
    except Exception:
        return {"title": "", "artist": "", "album": "", "duration": 0}
