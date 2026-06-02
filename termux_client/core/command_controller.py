"""
core/command_controller.py
Maps CLI commands to client operations.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from config import SUPPORTED_AUDIO_EXTENSIONS, STP_LISTEN_PORT
from core.auth_manager import AuthManager
from core.audio_metadata import extract_metadata
from transfer.integrity import sha256_file
from transfer.stp_receiver import serve_once


class CommandController:
    def __init__(self, api, auth: AuthManager):
        self.api = api
        self.auth = auth

    @staticmethod
    def help_text() -> str:
        return """
Commands:
  help
  register <username> <password> [display_name]
  login <username> <password>
  logout
  profile
  publish <path_to_audio>
  search <query>
  download <music_id> [filename]
  history [download|publish|login|logs]
  serve-stp [port]
  exit
""".strip()

    async def execute(self, line: str) -> bool:
        try:
            args = shlex.split(line)
        except ValueError as exc:
            print(f"Parse error: {exc}")
            return True

        if not args:
            return True

        cmd = args[0].lower()

        try:
            if cmd in {"exit", "quit"}:
                return False
            if cmd == "help":
                print(self.help_text())
            elif cmd == "register":
                await self._register(args)
            elif cmd == "login":
                await self._login(args)
            elif cmd == "logout":
                await self._logout()
            elif cmd == "profile":
                print(self.auth.get_profile())
            elif cmd == "publish":
                await self._publish(args)
            elif cmd == "search":
                await self._search(args)
            elif cmd == "download":
                await self._download(args)
            elif cmd == "history":
                await self._history(args)
            elif cmd == "serve-stp":
                self._serve_stp(args)
            else:
                print(f"Unknown command: {cmd}. Type 'help'.")
        except Exception as exc:
            print(f"ERROR: {exc}")
        return True

    async def _register(self, args: list[str]) -> None:
        if len(args) < 3:
            print("Usage: register <username> <password> [display_name]")
            return
        display_name = args[3] if len(args) >= 4 else args[1]
        data = await self.api.register(args[1], args[2], display_name)
        print("Registered:", data)

    async def _login(self, args: list[str]) -> None:
        if len(args) != 3:
            print("Usage: login <username> <password>")
            return
        data = await self.api.login(args[1], args[2])
        print("Login OK. user_id=", data.get("user_id"))

    async def _logout(self) -> None:
        data = await self.api.logout()
        print("Logout OK:", data)

    async def _publish(self, args: list[str]) -> None:
        if len(args) != 2:
            print("Usage: publish <path_to_audio>")
            return

        path = Path(args[1]).expanduser().resolve()
        if not path.exists():
            print(f"File not found: {path}")
            return

        ext = path.suffix.lower()
        mime = SUPPORTED_AUDIO_EXTENSIONS.get(ext, "application/octet-stream")
        meta = extract_metadata(path)
        file_hash = sha256_file(path)

        payload = {
            "filename": path.name,
            "mime_type": mime,
            "size": path.stat().st_size,
            "hmac_hash": file_hash,
            "stp_port": STP_LISTEN_PORT,
            # Extra fields are useful if your server schema accepts them.
            "title": meta.get("title") or path.stem,
            "artist": meta.get("artist") or "",
            "album": meta.get("album") or "",
            "duration": meta.get("duration") or 0,
        }
        data = await self.api.publish(payload)
        print("Published:", data)

    async def _search(self, args: list[str]) -> None:
        if len(args) < 2:
            print("Usage: search <query>")
            return
        query = " ".join(args[1:])
        data = await self.api.search(query)
        songs = data.get("songs", data if isinstance(data, list) else [])
        if not songs:
            print("No songs found.")
            return
        for song in songs:
            print(f"- {song.get('music_id')} | {song.get('filename')} | owner={song.get('owner')}")

    async def _download(self, args: list[str]) -> None:
        if len(args) < 2:
            print("Usage: download <music_id> [filename]")
            return
        music_id = args[1]
        data = await self.api.download(music_id)
        print("Download negotiation result:", data)
        print("Start STP receiver/sender according to your server negotiation flow.")
        print("If this peer will receive a file, run: serve-stp")

    async def _history(self, args: list[str]) -> None:
        history_type = args[1] if len(args) >= 2 else "download"
        data = await self.api.history(history_type)
        print(data)

    def _serve_stp(self, args: list[str]) -> None:
        port = int(args[1]) if len(args) >= 2 else STP_LISTEN_PORT
        serve_once(port=port)
