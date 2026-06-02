"""
core/transfer_manager.py
Orchestrates P2P transfers: negotiates with server, then runs STP.

Bridges PySide6 signals with asyncio using a dedicated event loop
running in a background QThread.
"""

import asyncio
import threading
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from PySide6.QtCore import QObject, Signal

from core.api_client import APIClient
from core.stp_downloader import STPDownloader
from core.stp_provider import STPProvider
from config import STP_LISTEN_PORT


class TransferDirection(Enum):
    UPLOAD = auto()
    DOWNLOAD = auto()


class TransferStatus(Enum):
    PENDING = auto()
    ACTIVE = auto()
    PAUSED = auto()
    DONE = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class TransferItem:
    transfer_id: str
    music_id: str
    filename: str
    direction: TransferDirection
    total_chunks: int = 0
    done_chunks: int = 0
    status: TransferStatus = TransferStatus.PENDING
    error: str = ""
    local_path: str = ""


class TransferManager(QObject):
    """
    Manages all active and queued transfers.

    Signals
    ───────
    progress_updated(transfer_id, done, total)
    transfer_done(transfer_id, local_path)
    transfer_failed(transfer_id, reason)
    """

    progress_updated = Signal(str, int, int)   # id, done, total
    transfer_done = Signal(str, str)           # id, local_path
    transfer_failed = Signal(str, str)         # id, reason

    def __init__(self, api: APIClient, parent=None):
        super().__init__(parent)
        self._api = api
        self._items: dict[str, TransferItem] = {}
        self._downloaders: dict[str, STPDownloader] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        # music_id -> local file path for songs published by this client.
        # STPProvider uses this to serve download requests from other peers.
        self._shared_files: dict[str, str] = {}
        self._provider: Optional[STPProvider] = None

    # ─── Asyncio thread ──────────────────────────────────────────────────────

    def start(self):
        """Start background asyncio loop for network I/O."""
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_start())
        self._loop.run_forever()

    async def _async_start(self):
        await self._api.start()
        self._provider = STPProvider(
            api=self._api,
            shared_files=self._shared_files,
            listen_port=STP_LISTEN_PORT,
            progress_cb=self._on_provider_progress,
            error_cb=self._on_provider_error,
        )
        await self._provider.start()

    def stop(self):
        if self._loop:
            if self._provider:
                asyncio.run_coroutine_threadsafe(self._provider.stop(), self._loop)
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _submit(self, coro):
        """Submit a coroutine to the background loop."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ─── Shared files / provider registry ────────────────────────────────────

    def register_shared_file(self, music_id: str, local_path: str):
        """
        Remember that this client owns a published song.

        Called after /publish returns music_id. Without this mapping, the peer
        can be online but cannot send the actual file to a downloader.
        """
        if not music_id or not local_path:
            return
        self._shared_files[music_id] = local_path
        if self._provider:
            self._provider.register_file(music_id, local_path)
        print(f"[SHARED FILE] {music_id} -> {local_path}")

    # ─── Download ────────────────────────────────────────────────────────────

    def request_download(self, music_id: str, filename: str):
        tid = f"dl_{music_id}"
        item = TransferItem(
            transfer_id=tid,
            music_id=music_id,
            filename=filename,
            direction=TransferDirection.DOWNLOAD,
        )
        self._items[tid] = item
        self._submit(self._do_download(item))

    async def _do_download(self, item: TransferItem):
        item.status = TransferStatus.ACTIVE
        try:
            # 1. Negotiate with server: get provider peer address + peer_token.
            neg = await self._api.request_download(item.music_id)
            print("[DOWNLOAD NEG]", neg)

            peer_ip = neg["peer_ip"]
            peer_port = int(neg["peer_port"])
            peer_token = neg["peer_token"]

            # 2. Connect directly to provider peer and receive file.
            downloader = STPDownloader(
                peer_ip=peer_ip,
                peer_port=peer_port,
                peer_token=peer_token,
                music_id=item.music_id,
                filename=item.filename,
                progress_cb=self._on_download_progress,
                done_cb=self._on_download_done,
                error_cb=self._on_download_error,
            )
            self._downloaders[item.transfer_id] = downloader

            path = await downloader.download()
            item.status = TransferStatus.DONE
            item.local_path = path

        except Exception as exc:
            item.status = TransferStatus.FAILED
            item.error = str(exc)
            self.transfer_failed.emit(item.transfer_id, str(exc))
        finally:
            self._downloaders.pop(item.transfer_id, None)

    def cancel_transfer(self, transfer_id: str):
        if transfer_id in self._downloaders:
            self._downloaders[transfer_id].cancel()
        if transfer_id in self._items:
            self._items[transfer_id].status = TransferStatus.CANCELLED

    # ─── Download callbacks (called from asyncio thread) ─────────────────────

    def _on_download_progress(self, done: int, total: int, music_id: str):
        tid = f"dl_{music_id}"
        if tid in self._items:
            self._items[tid].done_chunks = done
            self._items[tid].total_chunks = total
        self.progress_updated.emit(tid, done, total)

    def _on_download_done(self, music_id: str, path: str):
        tid = f"dl_{music_id}"
        if tid in self._items:
            self._items[tid].status = TransferStatus.DONE
            self._items[tid].local_path = path
        self.transfer_done.emit(tid, path)

    def _on_download_error(self, music_id: str, reason: str):
        tid = f"dl_{music_id}"
        if tid in self._items:
            self._items[tid].status = TransferStatus.FAILED
            self._items[tid].error = reason
        self.transfer_failed.emit(tid, reason)

    # ─── Provider callbacks ──────────────────────────────────────────────────

    def _on_provider_progress(self, music_id: str, done: int, total: int):
        # Optional: upload progress can be displayed later if UI supports it.
        print(f"[STP PROVIDER] sent {music_id}: {done}/{total}")

    def _on_provider_error(self, music_id: str, reason: str):
        print(f"[STP PROVIDER ERROR] {music_id}: {reason}")

    # ─── Getters ─────────────────────────────────────────────────────────────

    def get_items(self) -> list[TransferItem]:
        return list(self._items.values())

    def get_item(self, transfer_id: str) -> Optional[TransferItem]:
        return self._items.get(transfer_id)

    # ─── API passthrough (convenience) ───────────────────────────────────────

    def submit_api(self, coro) -> "Future":
        return self._submit(coro)
