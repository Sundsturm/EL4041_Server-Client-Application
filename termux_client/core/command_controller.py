"""
core/command_controller.py
Maps CLI commands to client operations.

Changes:
- login/register: step-by-step interactive prompts (no inline args).
- edit-profile: step-by-step per-field (Enter to keep current value), login-gated.
- delete-profile: confirmation prompt + password, login-gated.
- profile: formatted display with server refresh.
- whoami: quick identity display.
- Improved search/history/download output.
- All auth-required commands gate on is_logged_in().
"""

from __future__ import annotations

import getpass
import shlex
from pathlib import Path

from config import SUPPORTED_AUDIO_EXTENSIONS, STP_LISTEN_PORT
from core.auth_manager import AuthManager
from core.audio_metadata import extract_metadata
from transfer.integrity import sha256_file
from transfer.stp_receiver import serve_once


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _prompt(label: str, secret: bool = False) -> str:
    """Read a line from stdin, optionally hiding input (password)."""
    if secret:
        return getpass.getpass(f"  {label}: ")
    return input(f"  {label}: ").strip()


def _prompt_optional(label: str, current: str = "", secret: bool = False) -> str:
    """
    Prompt user for a field, showing the current value.
    Returns current value unchanged if user presses Enter without typing.
    """
    hint = f"[current: {current}] " if current else ""
    if secret:
        raw = getpass.getpass(f"  {label} {hint}(Enter to keep): ")
    else:
        raw = input(f"  {label} {hint}(Enter to keep): ").strip()
    return raw if raw else current


def _hr(char: str = "─", width: int = 48) -> str:
    return char * width


# ─── Controller ──────────────────────────────────────────────────────────────

class CommandController:
    def __init__(self, api, auth: AuthManager):
        self.api = api
        self.auth = auth

    @staticmethod
    def help_text() -> str:
        return (
            _hr() + "\n"
            "  COMMANDS\n" +
            _hr() + "\n"
            "  Auth\n"
            "    register          — create a new account (interactive)\n"
            "    login             — sign in (interactive)\n"
            "    logout            — sign out\n"
            "\n"
            "  Profile  [login required]\n"
            "    whoami            — show current user\n"
            "    profile           — fetch full profile from server\n"
            "    edit-profile      — edit profile fields (interactive)\n"
            "    delete-profile    — delete account (with confirmation)\n"
            "\n"
            "  Music  [login required]\n"
            "    publish <path>    — publish an audio file\n"
            "    search  <query>   — search songs\n"
            "    download <id>     — initiate file download\n"
            "    history [type]    — download|publish|login|logs\n"
            "    serve-stp [port]  — receive a file over STP\n"
            "\n"
            "  Misc\n"
            "    status            — show connection & session info\n"
            "    help              — show this message\n"
            "    exit / quit       — close the client\n" +
            _hr()
        )

    # ─── Main dispatcher ─────────────────────────────────────────────────────

    async def execute(self, line: str) -> bool:
        try:
            args = shlex.split(line)
        except ValueError as exc:
            print(f"  Parse error: {exc}")
            return True

        if not args:
            return True

        cmd = args[0].lower()

        try:
            # --- Exit ---
            if cmd in {"exit", "quit"}:
                if self.auth.is_logged_in():
                    print("  Logging out before exit…")
                    try:
                        await self.api.logout()
                    except Exception:
                        self.auth.logout_local()
                print("  Goodbye! ♪")
                return False

            # --- Misc ---
            elif cmd == "help":
                print(self.help_text())
            elif cmd == "status":
                self._cmd_status()

            # --- Auth ---
            elif cmd == "register":
                await self._register()
            elif cmd == "login":
                await self._login()
            elif cmd == "logout":
                await self._logout()

            # --- Profile (login required) ---
            elif cmd == "whoami":
                self._cmd_whoami()
            elif cmd == "profile":
                await self._profile()
            elif cmd == "edit-profile":
                await self._edit_profile()
            elif cmd == "delete-profile":
                await self._delete_profile()

            # --- Music (login required) ---
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
                print(f"  Unknown command: '{cmd}'. Type 'help'.")

        except Exception as exc:
            print(f"  ERROR: {exc}")

        return True

    # ─── Auth commands ────────────────────────────────────────────────────────

    async def _register(self) -> None:
        """Interactive step-by-step registration."""
        print(_hr())
        print("  REGISTER")
        print(_hr())
        username = _prompt("Username")
        if not username:
            print("  Cancelled.")
            return
        password = _prompt("Password", secret=True)
        if not password:
            print("  Cancelled.")
            return

        print("  Registering…")
        data = await self.api.register(username, password)
        print(_hr())
        print(f"  ✓ Account created!")
        print(f"    Username : {username}")
        print(f"    User ID  : {data.get('user_id', 'N/A')}")
        print("  You can now run 'login' to sign in.")
        print(_hr())

    async def _login(self) -> None:
        """Interactive step-by-step login."""
        if self.auth.is_logged_in():
            print(f"  Already logged in as {self.auth.get_username()}.")
            print("  Run 'logout' first to switch accounts.")
            return

        print(_hr())
        print("  LOGIN")
        print(_hr())
        username = _prompt("Username")
        if not username:
            print("  Cancelled.")
            return
        password = _prompt("Password", secret=True)
        if not password:
            print("  Cancelled.")
            return

        print("  Signing in…")
        data = await self.api.login(username, password)

        print(_hr())
        print(f"  ✓ Welcome back, {self.auth.get_username()}!")
        print(f"    User ID  : {data.get('user_id', 'N/A')}")
        print(_hr())

    async def _logout(self) -> None:
        if not self.auth.is_logged_in():
            print("  Not logged in.")
            return
        name = self.auth.get_username()
        data = await self.api.logout()
        print(f"  ✓ Signed out. Goodbye, {name}!")

    # ─── Profile commands ─────────────────────────────────────────────────────

    def _cmd_whoami(self) -> None:
        if not self.auth.is_logged_in():
            print("  Not logged in.")
            return
        profile = self.auth.get_profile()
        print(_hr())
        print("  CURRENT USER")
        print(_hr())
        print(f"    Username : {profile.get('username', 'N/A')}")
        print(f"    User ID  : {profile.get('user_id', 'N/A')}")
        print(_hr())

    async def _profile(self) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in to view your profile.")
            return
        print("  Fetching profile…")
        data = await self.api.get_profile()
        print(_hr())
        print("  PROFILE")
        print(_hr())
        print(f"    Username   : {data.get('username', 'N/A')}")
        print(f"    User ID    : {data.get('user_id', 'N/A')}")
        print(f"    Bio        : {data.get('bio') or '—'}")
        print(f"    Created at : {data.get('created_at', 'N/A')}")
        print(_hr())

    async def _edit_profile(self) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in to edit your profile.")
            return

        # Try to fetch current values from server
        try:
            current = await self.api.get_profile()
        except Exception:
            current = self.auth.get_profile()

        print(_hr())
        print("  EDIT PROFILE  (press Enter to keep current value)")
        print(_hr())

        new_username = _prompt_optional(
            "Username", current=current.get("username", ""))
        new_password = _prompt_optional(
            "New password", secret=True)

        # Build payload with only changed fields
        payload: dict = {}
        if new_username and new_username != current.get("username"):
            payload["username"] = new_username
        if new_password:
            payload["password"] = new_password

        if not payload:
            print("  No changes made.")
            return

        print("  Saving changes…")
        data = await self.api.update_profile(**payload)
        # Update local cache
        profile = self.auth.get_profile()
        if "username" in payload:
            profile["username"] = payload["username"]
        self.auth.save_profile(profile)
        print(f"  ✓ Profile updated.")
        print(_hr())

    async def _delete_profile(self) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in to delete your profile.")
            return

        username = self.auth.get_username()
        print(_hr())
        print(f"  DELETE ACCOUNT — @{username}")
        print(_hr())
        print("  WARNING: This action is irreversible.")
        print("  Your profile, tokens, songs, and history will be permanently removed.")
        print()

        confirm = input("  Are you sure you want to delete this profile? (y/n): ").strip().lower()
        if confirm != "y":
            print("  Deletion cancelled.")
            return

        password = _prompt("Confirm your password", secret=True)
        if not password:
            print("  Deletion cancelled.")
            return

        print("  Deleting account…")
        await self.api.delete_profile(password)
        print("  ✓ Account deleted. You have been signed out.")
        print(_hr())

    # ─── Status ──────────────────────────────────────────────────────────────

    def _cmd_status(self) -> None:
        print(_hr())
        print("  STATUS")
        print(_hr())
        if self.auth.is_logged_in():
            profile = self.auth.get_profile()
            print(f"  Session : Active")
            print(f"  User    : {profile.get('username', 'N/A')}")
            print(f"  User ID : {profile.get('user_id', 'N/A')}")
        else:
            print("  Session : Not logged in")
        from config import SERVER_REST_BASE_URL
        print(f"  Server  : {SERVER_REST_BASE_URL}")
        print(_hr())

    # ─── Music commands ───────────────────────────────────────────────────────

    async def _publish(self, args: list[str]) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in to publish.")
            return
        if len(args) != 2:
            print("  Usage: publish <path_to_audio>")
            return

        path = Path(args[1]).expanduser().resolve()
        if not path.exists():
            print(f"  File not found: {path}")
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
            "title": meta.get("title") or path.stem,
            "artist": meta.get("artist") or "",
            "album": meta.get("album") or "",
            "duration": meta.get("duration") or 0,
        }
        print(f"  Publishing '{path.name}'…")
        data = await self.api.publish(payload)
        print(_hr())
        print(f"  ✓ Song published!")
        print(f"    Music ID : {data.get('music_id', 'N/A')}")
        print(f"    Title    : {data.get('title') or path.stem}")
        print(_hr())

    async def _search(self, args: list[str]) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in to search.")
            return
        if len(args) < 2:
            print("  Usage: search <query>")
            return

        query = " ".join(args[1:])
        print(f"  Searching for '{query}'…")
        data = await self.api.search(query)
        songs = data.get("songs", data if isinstance(data, list) else [])

        print(_hr())
        if not songs:
            print("  No songs found.")
        else:
            print(f"  RESULTS  ({len(songs)} song{'s' if len(songs) != 1 else ''})")
            print(_hr())
            for i, song in enumerate(songs, 1):
                title  = song.get("title") or song.get("filename") or "Unknown"
                artist = song.get("artist") or "Unknown artist"
                album  = song.get("album") or ""
                mid    = song.get("music_id") or "?"
                owner  = song.get("owner") or "?"
                album_str = f" / {album}" if album else ""
                print(f"  [{i}] {title} — {artist}{album_str}")
                print(f"      ID: {mid}   Owner: {owner}")
        print(_hr())

    async def _download(self, args: list[str]) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in to download.")
            return
        if len(args) < 2:
            print("  Usage: download <music_id>")
            return

        music_id = args[1]
        print(f"  Requesting download for ID: {music_id}…")
        data = await self.api.download(music_id)
        print(_hr())
        print("  ✓ Download negotiation complete.")
        print(f"    Peer IP   : {data.get('peer_ip', 'N/A')}")
        print(f"    Peer Port : {data.get('peer_port', 'N/A')}")
        print(f"    Peer ID   : {data.get('peer_id', 'N/A')}")
        print("  Run 'serve-stp' on this device to receive the file.")
        print(_hr())

    async def _history(self, args: list[str]) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in to view history.")
            return

        history_type = args[1] if len(args) >= 2 else "download"
        valid_types = {"download", "publish", "login", "logs"}
        if history_type not in valid_types:
            print(f"  Invalid type. Choose from: {', '.join(sorted(valid_types))}")
            return

        print(f"  Fetching '{history_type}' history…")
        data = await self.api.history(history_type)
        records = data if isinstance(data, list) else data.get("records", data.get("logs", []))

        print(_hr())
        print(f"  HISTORY — {history_type.upper()}")
        print(_hr())
        if not records:
            print("  No records found.")
        else:
            for rec in records:
                if isinstance(rec, dict):
                    ts  = rec.get("created_at") or rec.get("timestamp", "")
                    msg = (
                        rec.get("action") or rec.get("event") or
                        rec.get("filename") or rec.get("music_id") or str(rec)
                    )
                    print(f"  [{ts}] {msg}")
                else:
                    print(f"  {rec}")
        print(_hr())

    def _serve_stp(self, args: list[str]) -> None:
        port = int(args[1]) if len(args) >= 2 else STP_LISTEN_PORT
        print(f"  Starting STP receiver on port {port}…")
        serve_once(port=port)
