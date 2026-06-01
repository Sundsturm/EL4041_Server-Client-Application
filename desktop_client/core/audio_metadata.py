from mutagen import File


def extract_metadata(path: str) -> dict:
    """
    Extract metadata from audio file.
    Returns title, artist, album, duration.
    """

    try:
        audio = File(path, easy=True)

        if audio is None:
            return {}

        metadata = {
            "title": "",
            "artist": "",
            "album": "",
            "duration": 0
        }

        metadata["title"] = (
            audio.get("title", [""])[0]
            if "title" in audio
            else ""
        )

        metadata["artist"] = (
            audio.get("artist", [""])[0]
            if "artist" in audio
            else ""
        )

        metadata["album"] = (
            audio.get("album", [""])[0]
            if "album" in audio
            else ""
        )

        try:
            metadata["duration"] = int(audio.info.length)
        except Exception:
            pass

        return metadata

    except Exception:
        return {}