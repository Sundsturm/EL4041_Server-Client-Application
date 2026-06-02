"""
core/transfer_manager.py
Orchestrates P2P transfers: request approval, long-poll owner requests,
and run STP sender/receiver in a background asyncio loop.
"""

import asyncio
import json
import os
import threading
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from PySide6.QtCore import QObject, Signal

from config import BASE_DIR, STP_LISTEN_PORT
from core.api_client import APIClient
from core.stp_downloader import STPDownloader
from core.stp_provider import STPProvider


class TransferDirection(Enum):
    UPLOAD = auto()
    DOWNLOAD = auto()


class TransferStatus(Enum):
    PENDING = auto()
    APPROVED = auto()
    ACTIVE = auto()
    PAUSED = auto()
    DONE = auto()
    FAILED = auto()
    REJECTED = auto()
    CANCELLED = auto()


@dataclass
class TransferItem:
    transfer_id: str
    music_id: str
    filename: str
    direction: TransferDirection
    request_id: str = ""
    total_chunks: int = 0
    done_chunks: int = 0
    status: TransferStatus = TransferStatus.PENDING
    error: str = ""
    local_path: str = ""


class TransferManager(QObject):
    """
    Signals
    ───────
    progress_updated(transfer_id, done, total)
    upload_progress(transfer_id, done, total)
    transfer_done(transfer_id, local_path)
    transfer_failed(transfer_id, reason)
    new_requests_received(requests)
    request_approved(request_id)
    request_rejected(request_id, reason)
    """

    progress_updated = Signal(str, int, int)
    upload_progress = Signal(str, int, int)
    transfer_done = Signal(str, str)
    transfer_failed = Signal(str, str)

    new_requests_received = Signal(list)
    request_approved = Signal(str)
    request_rejected = Signal(str, str)

    def __init__(self, api: APIClient, parent=None):
        super().__init__(parent)
        self._api = api
        self._items: dict[str, TransferItem] = {}
        self._downloaders: dict[str, STPDownloader] = {}
        self._providers: dict[str, STPProvider] = {}

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        # music_id -> local file path
        self._shared_files: dict[str, str] = {}
        self._catalog_path = os.path.join(BASE_DIR, "settings", "shared_catalog.json")

        self._polling = False
        self._polling_task = None

        self._load_shared_catalog()

    # ─── Asyncio thread ──────────────────────────────────────────────────────

    def start(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_start())
        self._loop.run_forever()

    async def _async_start(self):
        await self._api.start()

    def stop(self):
        self._polling = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def submit_api(self, coro) -> "Future":
        return self._submit(coro)

    # ─── Shared file registry ────────────────────────────────────────────────

    def _load_shared_catalog(self):
        try:
            if os.path.exists(self._catalog_path):
                with open(self._catalog_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._shared_files.update({
                        str(k): str(v)
                        for k, v in data.items()
                        if isinstance(k, str) and isinstance(v, str)
                    })
        except Exception as exc:
            print(f"[CATALOG] load failed: {exc}")

    def _save_shared_catalog(self):
        try:
            os.makedirs(os.path.dirname(self._catalog_path), exist_ok=True)
            with open(self._catalog_path, "w", encoding="utf-8") as f:
                json.dump(self._shared_files, f, indent=2)
        except Exception as exc:
            print(f"[CATALOG] save failed: {exc}")

    def register_shared_file(self, music_id: str, local_path: str):
        if not music_id or not local_path:
            return
        self._shared_files[music_id] = local_path
        self._save_shared_catalog()
        print(f"[SHARED FILE] {music_id} -> {local_path}")

    def get_shared_file(self, music_id: str) -> str:
        path = self._shared_files.get(music_id, "")
        if path and os.path.exists(path):
            return path
        return ""

    # ─── Downloader side ─────────────────────────────────────────────────────

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
        item.status = TransferStatus.PENDING

        try:
            # 1. Submit request. Server returns request_id, not peer_ip directly.
            data = await self._api.request_download(item.music_id, STP_LISTEN_PORT)
            print("[DOWNLOAD REQUEST]", data)

            request_id = data.get("request_id", "")
            if not request_id:
                raise RuntimeError(f"download request did not return request_id: {data}")

            item.request_id = request_id
            item.status = TransferStatus.PENDING

            # 2. Listen for owner/provider connection.
            downloader = STPDownloader(
                listen_port=STP_LISTEN_PORT,
                music_id=item.music_id,
                filename=item.filename,
                progress_cb=self._on_download_progress,
                done_cb=self._on_download_done,
                error_cb=self._on_download_error,
            )
            self._downloaders[item.transfer_id] = downloader

            item.status = TransferStatus.ACTIVE
            path = await downloader.listen_and_receive()

            item.status = TransferStatus.DONE
            item.local_path = path
            await self._api.update_transfer_status(request_id, "completed")

        except Exception as exc:
            item.status = TransferStatus.FAILED
            item.error = str(exc)

            if item.request_id:
                try:
                    await self._api.update_transfer_status(item.request_id, "failed")
                except Exception:
                    pass

            self.transfer_failed.emit(item.transfer_id, str(exc))

        finally:
            self._downloaders.pop(item.transfer_id, None)

    # ─── Owner side long-polling ─────────────────────────────────────────────

    def start_requests_polling(self):
        if self._polling:
            return
        self._polling = True
        self._polling_task = self._submit(self._poll_pending_requests())

    def stop_requests_polling(self):
        self._polling = False

    async def _poll_pending_requests(self):
        await asyncio.sleep(1.0)

        while self._polling:
            try:
                data = await self._api.get_pending_requests(timeout=28)
                requests = data.get("requests", [])
                if requests:
                    self.new_requests_received.emit(requests)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[POLL] Error: {exc}")
                await asyncio.sleep(5)

    async def approve_and_send(
        self,
        request_id: str,
        file_path: str,
        requester_ip: str,
        requester_port: int,
        music_id: str,
        peer_token: str,
        mime_type: str = "audio/mpeg",
        filename: str = "",
    ):
        tid = f"upload_{request_id}"
        item = TransferItem(
            transfer_id=tid,
            music_id=music_id,
            filename=filename or os.path.basename(file_path),
            direction=TransferDirection.UPLOAD,
            request_id=request_id,
            local_path=file_path,
            status=TransferStatus.ACTIVE,
        )
        self._items[tid] = item

        provider = STPProvider(
            progress_cb=lambda mid, done, total: self._on_upload_progress(tid, done, total),
            error_cb=lambda mid, reason: self._on_upload_error(tid, reason),
        )
        self._providers[tid] = provider

        try:
            await self._api.update_transfer_status(request_id, "in_progress")

            await provider.send_one(
                peer_ip=requester_ip,
                peer_port=int(requester_port),
                file_path=file_path,
                music_id=music_id,
                peer_token=peer_token,
                mime_type=mime_type,
                filename=filename or os.path.basename(file_path),
                request_id=request_id,
            )

            item.status = TransferStatus.DONE
            await self._api.update_transfer_status(request_id, "completed")
            self.transfer_done.emit(tid, file_path)

        except Exception as exc:
            item.status = TransferStatus.FAILED
            item.error = str(exc)

            try:
                await self._api.update_transfer_status(request_id, "failed")
            except Exception:
                pass

            self.transfer_failed.emit(tid, str(exc))

        finally:
            self._providers.pop(tid, None)

    # ─── Cancel ──────────────────────────────────────────────────────────────

    def cancel_transfer(self, transfer_id: str):
        if transfer_id in self._downloaders:
            self._downloaders[transfer_id].cancel()
        if transfer_id in self._providers:
            self._providers[transfer_id].cancel()
        if transfer_id in self._items:
            self._items[transfer_id].status = TransferStatus.CANCELLED

    # ─── Download callbacks ─────────────────────────────────────────────────

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

    # ─── Upload callbacks ───────────────────────────────────────────────────

    def _on_upload_progress(self, transfer_id: str, done: int, total: int):
        if transfer_id in self._items:
            self._items[transfer_id].done_chunks = done
            self._items[transfer_id].total_chunks = total
        self.upload_progress.emit(transfer_id, done, total)

    def _on_upload_error(self, transfer_id: str, reason: str):
        if transfer_id in self._items:
            self._items[transfer_id].status = TransferStatus.FAILED
            self._items[transfer_id].error = reason
        self.transfer_failed.emit(transfer_id, reason)

    # ─── Getters ─────────────────────────────────────────────────────────────

    def get_items(self) -> list[TransferItem]:
        return list(self._items.values())

    def get_item(self, transfer_id: str) -> Optional[TransferItem]:
        return self._items.get(transfer_id)
