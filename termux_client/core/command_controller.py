"""
core/command_controller.py
Maps CLI commands to client operations.

New in this version:
- download: spawns asyncio background task (non-blocking STP receiver)
- requests: long-poll server for pending download requests (owner)
- approve:  owner approves + auto-send file via STP (background)
- reject:   owner rejects a request
- downloads: show in_progress / completed downloads (downloader)
- publish:  now also registers path in local catalog
"""

from __future__ import annotations

import asyncio
import getpass
import shlex
from pathlib import Path

from config import SUPPORTED_AUDIO_EXTENSIONS, STP_LISTEN_PORT
from core.auth_manager import AuthManager
from core.audio_metadata import extract_metadata
from core import catalog as music_catalog
from transfer.integrity import sha256_file
from transfer.stp_receiver import serve_once
from transfer.stp_sender import send_file_to_peer


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _prompt(label: str, secret: bool = False) -> str:
    if secret:
        return getpass.getpass(f"  {label}: ")
    return input(f"  {label}: ").strip()


def _prompt_optional(label: str, current: str = "", secret: bool = False) -> str:
    hint = f"[current: {current}] " if current else ""
    if secret:
        raw = getpass.getpass(f"  {label} {hint}(Enter to keep): ")
    else:
        raw = input(f"  {label} {hint}(Enter to keep): ").strip()
    return raw if raw else current


def _hr(char: str = "─", width: int = 48) -> str:
    return char * width


def _status_icon(status: str) -> str:
    return {
        "in_progress": "⏳",
        "completed":   "✓",
        "failed":      "✗",
        "pending":     "…",
        "approved":    "▶",
        "rejected":    "✗",
    }.get(status, "?")


# ─── Controller ──────────────────────────────────────────────────────────────

class CommandController:
    def __init__(self, api, auth: AuthManager):
        self.api  = api
        self.auth = auth
        # Track background tasks keyed by request_id
        self._bg_tasks: dict[str, asyncio.Task] = {}

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
            "    catalog           — list your published songs (local catalog)\n"
            "    download <id>     — request download (non-blocking)\n"
            "    downloads         — show in-progress / completed downloads\n"
            "    requests          — (owner) list pending download requests\n"
            "    approve <req_id>  — (owner) approve and send file\n"
            "    reject  <req_id>  — (owner) reject a request\n"
            "    history [type]    — download|publish|login|logs\n"
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
            if cmd in {"exit", "quit"}:
                if self.auth.is_logged_in():
                    print("  Logging out before exit…")
                    try:
                        await self.api.logout()
                    except Exception:
                        self.auth.logout_local()
                # Cancel any background tasks
                for task in self._bg_tasks.values():
                    task.cancel()
                print("  Goodbye! ♪")
                return False

            elif cmd == "help":
                print(self.help_text())
            elif cmd == "status":
                self._cmd_status()

            elif cmd == "register":
                await self._register()
            elif cmd == "login":
                await self._login()
            elif cmd == "logout":
                await self._logout()

            elif cmd == "whoami":
                self._cmd_whoami()
            elif cmd == "profile":
                await self._profile()
            elif cmd == "edit-profile":
                await self._edit_profile()
            elif cmd == "delete-profile":
                await self._delete_profile()

            elif cmd == "publish":
                await self._publish(args)
            elif cmd == "search":
                await self._search(args)
            elif cmd == "catalog":
                self._cmd_catalog()
            elif cmd == "download":
                await self._download(args)
            elif cmd == "downloads":
                await self._downloads()
            elif cmd == "requests":
                await self._requests()
            elif cmd == "approve":
                await self._approve(args)
            elif cmd == "reject":
                await self._reject(args)
            elif cmd == "history":
                await self._history(args)

            else:
                print(f"  Unknown command: '{cmd}'. Type 'help'.")

        except Exception as exc:
            print(f"  ERROR: {exc}")

        return True

    # ─── Auth ─────────────────────────────────────────────────────────────────

    async def _register(self) -> None:
        print(_hr()); print("  REGISTER"); print(_hr())
        username = _prompt("Username")
        if not username:
            print("  Cancelled."); return
        password = _prompt("Password", secret=True)
        if not password:
            print("  Cancelled."); return
        print("  Registering…")
        data = await self.api.register(username, password)
        print(_hr())
        print(f"  ✓ Account created!")
        print(f"    Username : {username}")
        print(f"    User ID  : {data.get('user_id', 'N/A')}")
        print("  You can now run 'login' to sign in.")
        print(_hr())

    async def _login(self) -> None:
        if self.auth.is_logged_in():
            print(f"  Already logged in as {self.auth.get_username()}.")
            print("  Run 'logout' first to switch accounts."); return
        print(_hr()); print("  LOGIN"); print(_hr())
        username = _prompt("Username")
        if not username:
            print("  Cancelled."); return
        password = _prompt("Password", secret=True)
        if not password:
            print("  Cancelled."); return
        print("  Signing in…")
        data = await self.api.login(username, password)
        print(_hr())
        print(f"  ✓ Welcome back, {self.auth.get_username()}!")
        print(f"    User ID  : {data.get('user_id', 'N/A')}")
        print(_hr())

    async def _logout(self) -> None:
        if not self.auth.is_logged_in():
            print("  Not logged in."); return
        name = self.auth.get_username()
        await self.api.logout()
        print(f"  ✓ Signed out. Goodbye, {name}!")

    # ─── Profile ──────────────────────────────────────────────────────────────

    def _cmd_whoami(self) -> None:
        if not self.auth.is_logged_in():
            print("  Not logged in."); return
        profile = self.auth.get_profile()
        print(_hr()); print("  CURRENT USER"); print(_hr())
        print(f"    Username : {profile.get('username', 'N/A')}")
        print(f"    User ID  : {profile.get('user_id', 'N/A')}")
        print(_hr())

    async def _profile(self) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in to view your profile."); return
        print("  Fetching profile…")
        data = await self.api.get_profile()
        print(_hr()); print("  PROFILE"); print(_hr())
        print(f"    Username   : {data.get('username', 'N/A')}")
        print(f"    User ID    : {data.get('user_id', 'N/A')}")
        print(f"    Bio        : {data.get('bio') or '—'}")
        print(f"    Created at : {data.get('created_at', 'N/A')}")
        print(_hr())

    async def _edit_profile(self) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in to edit your profile."); return
        try:
            current = await self.api.get_profile()
        except Exception:
            current = self.auth.get_profile()
        print(_hr()); print("  EDIT PROFILE  (press Enter to keep current value)"); print(_hr())
        new_username = _prompt_optional("Username", current=current.get("username", ""))
        new_password = _prompt_optional("New password", secret=True)
        payload: dict = {}
        if new_username and new_username != current.get("username"):
            payload["username"] = new_username
        if new_password:
            payload["password"] = new_password
        if not payload:
            print("  No changes made."); return
        print("  Saving changes…")
        await self.api.update_profile(**payload)
        profile = self.auth.get_profile()
        if "username" in payload:
            profile["username"] = payload["username"]
        self.auth.save_profile(profile)
        print(f"  ✓ Profile updated.")
        print(_hr())

    async def _delete_profile(self) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in to delete your profile."); return
        username = self.auth.get_username()
        print(_hr()); print(f"  DELETE ACCOUNT — @{username}"); print(_hr())
        print("  WARNING: This action is irreversible.")
        confirm = input("  Are you sure? (y/n): ").strip().lower()
        if confirm != "y":
            print("  Deletion cancelled."); return
        password = _prompt("Confirm your password", secret=True)
        if not password:
            print("  Deletion cancelled."); return
        print("  Deleting account…")
        await self.api.delete_profile(password)
        print("  ✓ Account deleted. You have been signed out.")
        print(_hr())

    # ─── Status ───────────────────────────────────────────────────────────────

    def _cmd_status(self) -> None:
        print(_hr()); print("  STATUS"); print(_hr())
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
            print("  You must be logged in to publish."); return
        if len(args) != 2:
            print("  Usage: publish <path_to_audio>"); return

        path = Path(args[1]).expanduser().resolve()
        if not path.exists():
            print(f"  File not found: {path}"); return

        ext  = path.suffix.lower()
        mime = SUPPORTED_AUDIO_EXTENSIONS.get(ext, "application/octet-stream")
        meta = extract_metadata(path)
        file_hash = sha256_file(path)

        payload = {
            "filename":  path.name,
            "mime_type": mime,
            "size":      path.stat().st_size,
            "hmac_hash": file_hash,
            "stp_port":  STP_LISTEN_PORT,
            "title":     meta.get("title") or path.stem,
            "artist":    meta.get("artist") or "",
            "album":     meta.get("album") or "",
            "duration":  meta.get("duration") or 0,
        }
        print(f"  Publishing '{path.name}'…")
        data = await self.api.publish(payload)

        # Register in local catalog so approve can find the file later
        music_id = data.get("music_id", "")
        if music_id:
            music_catalog.register(music_id, path)

        print(_hr())
        print(f"  ✓ Song published!")
        print(f"    Music ID : {music_id or 'N/A'}")
        print(f"    Title    : {data.get('title') or path.stem}")
        print(_hr())

    async def _search(self, args: list[str]) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in to search."); return
        if len(args) < 2:
            print("  Usage: search <query>"); return

        query = " ".join(args[1:])
        print(f"  Searching for '{query}'…")
        data  = await self.api.search(query)
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

    def _cmd_catalog(self) -> None:
        """Show the local catalog of published songs."""
        entries = music_catalog.all_entries()
        print(_hr()); print("  LOCAL CATALOG"); print(_hr())
        if not entries:
            print("  No songs in local catalog. Publish a song first.")
        else:
            for mid, path in entries.items():
                exists = "✓" if Path(path).is_file() else "✗ MISSING"
                print(f"  {exists}  {mid}")
                print(f"        {path}")
        print(_hr())

    # ─── DOWNLOAD (downloader side) ───────────────────────────────────────────

    async def _download(self, args: list[str]) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in to download."); return
        if len(args) < 2:
            print("  Usage: download <music_id>"); return

        music_id = args[1]
        print(f"  Submitting download request for ID: {music_id}…")
        data = await self.api.download(music_id, requester_port=STP_LISTEN_PORT)

        request_id = data.get("request_id", "")
        song_title = data.get("song_title", music_id)

        print(_hr())
        print(f"  ✓ Request submitted!")
        print(f"    Song       : {song_title}")
        print(f"    Request ID : {request_id}")
        print(f"  Waiting for owner to approve…")
        print(f"  STP listener starting in background on port {STP_LISTEN_PORT}…")
        print(_hr())

        # Spawn non-blocking background task: wait for owner to connect
        loop = asyncio.get_event_loop()
        task = loop.create_task(
            self._bg_receive(request_id, music_id, song_title)
        )
        self._bg_tasks[request_id] = task

    async def _bg_receive(
        self, request_id: str, music_id: str, song_title: str
    ) -> None:
        """
        Background task: run blocking STP receiver in a thread executor,
        then update transfer status on the server.
        """
        loop = asyncio.get_event_loop()
        try:
            output_path = await loop.run_in_executor(
                None, lambda: serve_once(port=STP_LISTEN_PORT)
            )
            if output_path:
                print(f"\n  [✓ DOWNLOAD] '{song_title}' saved to: {output_path}")
                try:
                    await self.api.update_transfer_status(request_id, "completed")
                except Exception:
                    pass
            else:
                print(f"\n  [✗ DOWNLOAD] '{song_title}' transfer failed or cancelled.")
                try:
                    await self.api.update_transfer_status(request_id, "failed")
                except Exception:
                    pass
        except Exception as exc:
            print(f"\n  [✗ DOWNLOAD] '{song_title}' error: {exc}")
            try:
                await self.api.update_transfer_status(request_id, "failed")
            except Exception:
                pass
        finally:
            self._bg_tasks.pop(request_id, None)

    async def _downloads(self) -> None:
        """Show in_progress and completed downloads (downloader side)."""
        if not self.auth.is_logged_in():
            print("  You must be logged in."); return

        # Also show any active background tasks
        active_ids = set(self._bg_tasks.keys())

        data = await self.api.get_my_downloads()
        items = data.get("downloads", [])

        print(_hr()); print("  MY DOWNLOADS"); print(_hr())
        if not items and not active_ids:
            print("  No downloads yet. Use 'download <id>' to start.")
        else:
            for item in items:
                icon   = _status_icon(item.get("status", ""))
                title  = item.get("title") or item.get("filename") or "Unknown"
                artist = item.get("artist") or ""
                status = item.get("status", "")
                rid    = item.get("request_id", "")
                bg     = " [active]" if rid in active_ids else ""
                print(f"  {icon} {title}" + (f" — {artist}" if artist else ""))
                print(f"      Status: {status}{bg}   ID: {rid}")
            # Pending tasks not yet server-tracked
            for rid in active_ids:
                if not any(i.get("request_id") == rid for i in items):
                    print(f"  ⏳ [pending approval]   ID: {rid}")
        print(_hr())

    # ─── REQUESTS / APPROVE / REJECT (owner side) ─────────────────────────────

    async def _requests(self) -> None:
        """Long-poll server for pending download requests (owner)."""
        if not self.auth.is_logged_in():
            print("  You must be logged in."); return

        print("  Checking for pending requests (long-poll, up to 30 s)…")
        data = await self.api.get_pending_requests()
        reqs = data.get("requests", [])

        print(_hr()); print("  PENDING DOWNLOAD REQUESTS"); print(_hr())
        if not reqs:
            print("  No pending requests.")
        else:
            for i, req in enumerate(reqs, 1):
                title   = req.get("title") or req.get("filename") or "Unknown"
                artist  = req.get("artist") or ""
                who     = req.get("requester_name") or req.get("requester_id", "?")
                rid     = req.get("request_id", "")
                ts      = req.get("created_at", "")[:19]
                print(f"  [{i}] '{title}'" + (f" — {artist}" if artist else ""))
                print(f"       From : {who}   At: {ts}")
                print(f"       ID   : {rid}")
        print(_hr())
        if reqs:
            print("  Use 'approve <request_id>' or 'reject <request_id>'.")

    async def _approve(self, args: list[str]) -> None:
        """Owner approves a request and sends the file in the background."""
        if not self.auth.is_logged_in():
            print("  You must be logged in."); return
        if len(args) < 2:
            print("  Usage: approve <request_id>"); return

        request_id = args[1]
        print(f"  Approving request {request_id}…")
        data = await self.api.approve_transfer(request_id)

        music_id       = data.get("music_id", "")
        filename       = data.get("filename", "")
        title          = data.get("title") or filename
        requester_ip   = data.get("requester_ip", "")
        requester_port = int(data.get("requester_port", STP_LISTEN_PORT))
        peer_token     = data.get("peer_token", "")
        mime_type      = data.get("mime_type", "application/octet-stream")

        # Locate the file locally via catalog
        file_path = music_catalog.get_path(music_id)

        if file_path is None:
            # Prompt for manual path (max 2 attempts)
            print(f"  ⚠ File for '{title}' not found in local catalog.")
            for attempt in range(1, 3):
                raw = input(f"  Enter local path for '{filename}' (attempt {attempt}/2): ").strip()
                candidate = Path(raw).expanduser().resolve()
                if candidate.is_file():
                    music_catalog.update_path(music_id, candidate)
                    file_path = candidate
                    print(f"  Path saved to catalog.")
                    break
                else:
                    print(f"  File not found: {candidate}")

            if file_path is None:
                print("  Could not locate file. Auto-rejecting request…")
                try:
                    await self.api.reject_transfer(request_id, reason="file_not_found")
                except Exception:
                    pass
                print("  Request rejected. The downloader should retry with 'download <id>'.")
                return

        print(f"  ✓ Sending '{title}' → {requester_ip}:{requester_port} (background)…")

        loop = asyncio.get_event_loop()
        task = loop.create_task(
            self._bg_send(
                request_id=request_id,
                peer_ip=requester_ip,
                peer_port=requester_port,
                file_path=str(file_path),
                music_id=music_id,
                peer_token=peer_token,
                mime_type=mime_type,
                title=title,
            )
        )
        self._bg_tasks[f"send_{request_id}"] = task
        print("  CLI remains available. Progress will appear here as chunks are sent.")

    async def _bg_send(
        self,
        request_id: str,
        peer_ip: str,
        peer_port: int,
        file_path: str,
        music_id: str,
        peer_token: str,
        mime_type: str,
        title: str,
    ) -> None:
        """Background task: run blocking STP sender in a thread executor."""
        loop = asyncio.get_event_loop()
        try:
            # Update status to in_progress before starting
            await self.api.update_transfer_status(request_id, "in_progress")

            await loop.run_in_executor(
                None,
                lambda: send_file_to_peer(
                    peer_ip=peer_ip,
                    peer_port=peer_port,
                    file_path=file_path,
                    music_id=music_id,
                    peer_token=peer_token,
                    mime_type=mime_type,
                ),
            )
            print(f"\n  [✓ SENT] '{title}' delivered successfully.")
            await self.api.update_transfer_status(request_id, "completed")
        except Exception as exc:
            print(f"\n  [✗ SEND] '{title}' failed: {exc}")
            try:
                await self.api.update_transfer_status(request_id, "failed")
            except Exception:
                pass
        finally:
            self._bg_tasks.pop(f"send_{request_id}", None)

    async def _reject(self, args: list[str]) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in."); return
        if len(args) < 2:
            print("  Usage: reject <request_id>"); return

        request_id = args[1]
        reason = " ".join(args[2:]) if len(args) > 2 else ""
        await self.api.reject_transfer(request_id, reason=reason)
        print(f"  ✓ Request {request_id} rejected.")

    # ─── History ──────────────────────────────────────────────────────────────

    async def _history(self, args: list[str]) -> None:
        if not self.auth.is_logged_in():
            print("  You must be logged in to view history."); return

        history_type = args[1] if len(args) >= 2 else "download"
        valid_types  = {"download", "publish", "login", "logs"}
        if history_type not in valid_types:
            print(f"  Invalid type. Choose from: {', '.join(sorted(valid_types))}")
            return

        print(f"  Fetching '{history_type}' history…")
        data    = await self.api.history(history_type)
        records = data if isinstance(data, list) else data.get("records", data.get("logs", []))

        print(_hr()); print(f"  HISTORY — {history_type.upper()}"); print(_hr())
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
