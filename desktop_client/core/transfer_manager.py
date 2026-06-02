"""
core/transfer_manager.py
Orchestrates P2P transfers: negotiates with server, then runs STP.

Bridges PySide6 signals with asyncio using a dedicated event loop
running in a background QThread.
"""

import asyncio
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from core.api_client import APIClient
from core.stp_receiver import STPReceiver
from core.stp_sender import STPSender
from config import STP_LISTEN_PORT


class TransferDirection(Enum):
    UPLOAD   = auto()
    DOWNLOAD = auto()


class TransferStatus(Enum):
    PENDING    = auto()
    ACTIVE     = auto()
    PAUSED     = auto()
    DONE       = auto()
    FAILED     = auto()
    CANCELLED  = auto()


@dataclass
class TransferItem:
    transfer_id: str
    music_id:    str
    filename:    str
    direction:   TransferDirection
    total_chunks: int = 0
    done_chunks:  int = 0
    status:       TransferStatus = TransferStatus.PENDING
    error:        str = ""
    local_path:   str = ""


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
    transfer_done    = Signal(str, str)         # id, local_path
    transfer_failed  = Signal(str, str)         # id, reason

    def __init__(self, api: APIClient, parent=None):
        super().__init__(parent)
        self._api    = api
        self._items: dict[str, TransferItem] = {}
        self._senders: dict[str, STPSender]  = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        self._valid_tokens: set[str] = set()
        self._receiver: Optional[STPReceiver] = None

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
        self._receiver = STPReceiver(
            valid_tokens=self._valid_tokens,
            progress_cb=self._on_recv_progress,
            done_cb=self._on_recv_done,
            error_cb=self._on_recv_error,
            listen_port=STP_LISTEN_PORT,
        )
        await self._receiver.start()

    def stop(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _submit(self, coro):
        """Submit a coroutine to the background loop."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

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
            # Negotiate with server
            neg = await self._api.request_download(item.music_id)
            peer_ip    = neg["peer_ip"]
            peer_port  = neg["peer_port"]
            peer_token = neg["peer_token"]
            print("[DOWNLOAD NEG]", neg)

            # Register token with receiver
            self._valid_tokens.add(peer_token)

            # The sender (peer) will connect to us.
            # Nothing more to do here; STPReceiver handles the rest via callbacks.
            # But we need to tell the peer that we are ready (done via server signaling in real impl).
            # For direct P2P where WE connect TO the peer:
            # (depends on server NAT traversal; here we act as downloader that connects out)
            sender_as_downloader = STPSender(
                peer_ip=peer_ip,
                peer_port=peer_port,
                peer_token=peer_token,
                file_path="",          # Not sending, but STPSender is repurposed for DOWNLOAD_REQ flow
                music_id=item.music_id,
            )
            # Actually: in the project the downloader receives, sender sends.
            # So we just wait for the STPReceiver callback. Nothing else needed.
            _ = sender_as_downloader  # Remove if NAT allows passive receive

        except Exception as exc:
            item.status = TransferStatus.FAILED
            item.error  = str(exc)
            self.transfer_failed.emit(item.transfer_id, str(exc))

    # ─── Upload (Publish + send when peer requests) ───────────────────────────

    def start_upload(
        self,
        peer_ip: str,
        peer_port: int,
        peer_token: str,
        file_path: str,
        music_id: str,
    ):
        tid  = f"ul_{music_id}"
        item = TransferItem(
            transfer_id=tid,
            music_id=music_id,
            filename=file_path,
            direction=TransferDirection.UPLOAD,
            local_path=file_path,
        )
        self._items[tid] = item

        sender = STPSender(
            peer_ip=peer_ip,
            peer_port=peer_port,
            peer_token=peer_token,
            file_path=file_path,
            music_id=music_id,
            progress_cb=lambda done, total: self._on_send_progress(tid, done, total),
        )
        self._senders[tid] = sender
        self._submit(self._do_upload(item, sender))

    async def _do_upload(self, item: TransferItem, sender: STPSender):
        item.status = TransferStatus.ACTIVE
        try:
            await sender.send()
            item.status = TransferStatus.DONE
            self.transfer_done.emit(item.transfer_id, item.local_path)
        except Exception as exc:
            item.status = TransferStatus.FAILED
            item.error  = str(exc)
            self.transfer_failed.emit(item.transfer_id, str(exc))

    def cancel_transfer(self, transfer_id: str):
        if transfer_id in self._senders:
            self._senders[transfer_id].cancel()
        if transfer_id in self._items:
            self._items[transfer_id].status = TransferStatus.CANCELLED

    # ─── STPReceiver callbacks (called from asyncio thread) ──────────────────

    def _on_recv_progress(self, done: int, total: int, music_id: str):
        tid = f"dl_{music_id}"
        if tid in self._items:
            self._items[tid].done_chunks  = done
            self._items[tid].total_chunks = total
        self.progress_updated.emit(tid, done, total)

    def _on_recv_done(self, music_id: str, path: str):
        tid = f"dl_{music_id}"
        if tid in self._items:
            self._items[tid].status     = TransferStatus.DONE
            self._items[tid].local_path = path
        self.transfer_done.emit(tid, path)

    def _on_recv_error(self, music_id: str, reason: str):
        tid = f"dl_{music_id}"
        if tid in self._items:
            self._items[tid].status = TransferStatus.FAILED
            self._items[tid].error  = reason
        self.transfer_failed.emit(tid, reason)

    # ─── Send progress (called from asyncio thread) ──────────────────────────

    def _on_send_progress(self, tid: str, done: int, total: int):
        if tid in self._items:
            self._items[tid].done_chunks  = done
            self._items[tid].total_chunks = total
        self.progress_updated.emit(tid, done, total)

    # ─── Getters ─────────────────────────────────────────────────────────────

    def get_items(self) -> list[TransferItem]:
        return list(self._items.values())

    def get_item(self, transfer_id: str) -> Optional[TransferItem]:
        return self._items.get(transfer_id)

    # ─── API passthrough (convenience) ───────────────────────────────────────

    def submit_api(self, coro) -> "Future":
        return self._submit(coro)
