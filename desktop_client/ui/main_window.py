"""
ui/main_window.py
Main application window.

Layout
──────
┌──────────┬─────────────────────────────────────┐
│  SIDEBAR │  CONTENT AREA (QStackedWidget)       │
│          │                                       │
│  [nav]   │  login / search / publish / transfer  │
│  [nav]   │  history / peers                      │
│  [nav]   │                                       │
│          │                                       │
│ username │                                       │
└──────────┴───────────────────┬───────────────────┘
                               │ status bar
                               └──────────────────

All async work is submitted to TransferManager._loop via submit_api().
Results come back through Qt signals (thread-safe).
"""

import asyncio
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QStackedWidget, QStatusBar,
    QMessageBox, QFrame,
)

from core.api_client import APIClient, APIError
from core.auth_manager import AuthManager
from core.transfer_manager import TransferManager, TransferDirection

from ui.login_window import LoginWindow
from ui.publish_window import PublishWindow
from ui.search_window import SearchWindow
from ui.transfer_window import TransferWindow
from ui.history_window import HistoryWindow
from ui.peer_status_window import PeerStatusWindow


NAV_ITEMS = [
    ("◈  SEARCH",    0),
    ("⬆  PUBLISH",   1),
    ("⇄  TRANSFERS", 2),
    ("◎  PEERS",     3),
    ("☰  HISTORY",   4),
]


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self._auth    = AuthManager()
        self._api     = APIClient(self._auth)
        self._tm      = TransferManager(self._api, parent=self)
        self._nav_btns: list[QPushButton] = []

        self.setWindowTitle("STP Music — Desktop Client")
        self.resize(1100, 720)
        self._build_ui()
        self._connect_signals()
        self._tm.start()

        # Auto-login if session token exists
        if self._auth.is_logged_in():
            QTimer.singleShot(0, self._try_auto_login)
        else:
            QTimer.singleShot(0, self._show_login)

    # ─── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        root_lay = QHBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)
        self.setCentralWidget(root)

        # ── Sidebar ───────────────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setFixedWidth(180)
        sidebar.setStyleSheet("background-color: #080808; border-right: 1px solid #1A1A1A;")
        sb_lay = QVBoxLayout(sidebar)
        sb_lay.setContentsMargins(0, 0, 0, 0)
        sb_lay.setSpacing(0)

        # Logo
        logo = QLabel("◈ STP\nMUSIC")
        logo.setStyleSheet(
            "color: #00E5CC; font-size: 14px; font-weight: bold; "
            "letter-spacing: 4px; padding: 24px 16px 20px; "
            "border-bottom: 1px solid #1A1A1A;"
        )
        sb_lay.addWidget(logo)
        sb_lay.addSpacing(12)

        # Nav buttons
        for label, idx in NAV_ITEMS:
            btn = QPushButton(label)
            btn.setObjectName("nav")
            btn.setCheckable(True)
            btn.setFixedHeight(44)
            btn.clicked.connect(lambda checked, i=idx: self._navigate(i))
            sb_lay.addWidget(btn)
            self._nav_btns.append(btn)

        sb_lay.addStretch()

        # User info area
        self._lbl_user = QLabel("not signed in")
        self._lbl_user.setStyleSheet(
            "color: #444; font-size: 10px; padding: 12px 16px; "
            "border-top: 1px solid #1A1A1A; letter-spacing: 1px;"
        )
        self._lbl_user.setWordWrap(True)
        sb_lay.addWidget(self._lbl_user)

        self._btn_logout = QPushButton("SIGN OUT")
        self._btn_logout.setObjectName("nav")
        self._btn_logout.setEnabled(False)
        self._btn_logout.clicked.connect(self._on_logout)
        sb_lay.addWidget(self._btn_logout)
        sb_lay.addSpacing(8)

        root_lay.addWidget(sidebar)

        # ── Content stack ─────────────────────────────────────────────────
        self._search_win   = SearchWindow()
        self._publish_win  = PublishWindow()
        self._transfer_win = TransferWindow()
        self._peer_win     = PeerStatusWindow()
        self._history_win  = HistoryWindow()

        self._stack = QStackedWidget()
        for w in (
            self._search_win, self._publish_win,
            self._transfer_win, self._peer_win, self._history_win,
        ):
            self._stack.addWidget(w)

        root_lay.addWidget(self._stack)

        # Status bar
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("ready")
        self._navigate(0)

    # ─── Signal wiring ───────────────────────────────────────────────────────

    def _connect_signals(self):
        # Search
        self._search_win.search_requested.connect(self._on_search)
        self._search_win.download_requested.connect(self._on_download)

        # Publish
        self._publish_win.publish_requested.connect(self._on_publish)

        # History / Peers refresh
        self._history_win.refresh_requested.connect(self._on_refresh_history)
        self._peer_win.refresh_requested.connect(self._on_refresh_peers)

        # Transfer manager signals
        self._tm.progress_updated.connect(self._transfer_win.on_progress)
        self._tm.transfer_done.connect(self._on_transfer_done)
        self._tm.transfer_failed.connect(self._on_transfer_failed)

    # ─── Navigation ──────────────────────────────────────────────────────────

    def _navigate(self, idx: int):
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_btns):
            btn.setChecked(i == idx)

    # ─── Auth flow ───────────────────────────────────────────────────────────

    def _show_login(self):
        dlg = LoginWindow(self)
        dlg.login_requested.connect(self._on_login)
        dlg.register_requested.connect(self._on_register)
        dlg.exec()

    def _try_auto_login(self):
        session = self._auth.get_session_token()
        if not session:
            self._show_login()
            return
        future = self._tm.submit_api(self._api.refresh_session(session))
        self._status("refreshing session...")

        def _check():
            if future.done():
                timer.stop()
                try:
                    future.result()
                    self._on_auth_success()
                except Exception as e:
                    self._status(f"session expired: {e}", error=True)
                    self._show_login()

        timer = QTimer(self)
        timer.timeout.connect(_check)
        timer.start(200)

    def _on_login(self, username: str, password: str):
        future = self._tm.submit_api(self._api.login(username, password))
        self._status("signing in...")

        def _check():
            if future.done():
                timer.stop()
                try:
                    future.result()
                    self._on_auth_success()
                    # Close login dialog by accepting
                    for w in self.findChildren(LoginWindow):
                        w.accept()
                except APIError as e:
                    for w in self.findChildren(LoginWindow):
                        w.show_error(str(e))
                    self._status(f"login failed: {e}", error=True)

        timer = QTimer(self)
        timer.timeout.connect(_check)
        timer.start(200)

    def _on_register(self, username: str, password: str):
        future = self._tm.submit_api(self._api.register(username, password))
        self._status("registering...")

        def _check():
            if future.done():
                timer.stop()
                try:
                    future.result()
                    for w in self.findChildren(LoginWindow):
                        w.show_info("Account created! Please sign in.")
                    self._status("registration successful")
                except APIError as e:
                    for w in self.findChildren(LoginWindow):
                        w.show_error(str(e))

        timer = QTimer(self)
        timer.timeout.connect(_check)
        timer.start(200)

    def _on_auth_success(self):
        username = self._auth.get_username()
        self._lbl_user.setText(f"signed in as\n{username}")
        self._lbl_user.setStyleSheet(
            "color: #00E5CC; font-size: 10px; padding: 12px 16px; "
            "border-top: 1px solid #1A1A1A; letter-spacing: 1px;"
        )
        self._btn_logout.setEnabled(True)
        self._status(f"welcome, {username}")

    def _on_logout(self):
        future = self._tm.submit_api(self._api.logout())
        self._lbl_user.setText("not signed in")
        self._lbl_user.setStyleSheet(
            "color: #444; font-size: 10px; padding: 12px 16px; "
            "border-top: 1px solid #1A1A1A; letter-spacing: 1px;"
        )
        self._btn_logout.setEnabled(False)
        self._status("signed out")
        QTimer.singleShot(500, self._show_login)

    # ─── Search ──────────────────────────────────────────────────────────────

    def _on_search(self, query: str):
        future = self._tm.submit_api(self._api.search_songs(query))
        self._status(f"searching: {query!r}...")

        def _check():
            if future.done():
                timer.stop()
                try:
                    data  = future.result()
                    songs = data.get("songs", data) if isinstance(data, dict) else data
                    if not isinstance(songs, list):
                        songs = []
                    self._search_win.populate(songs)
                    self._status(f"found {len(songs)} result(s)")
                except APIError as e:
                    self._search_win.set_status(str(e), error=True)
                    self._status(str(e), error=True)

        timer = QTimer(self)
        timer.timeout.connect(_check)
        timer.start(300)

    # ─── Download ────────────────────────────────────────────────────────────

    def _on_download(self, music_id: str, filename: str):
        self._navigate(2)  # Switch to Transfers tab
        self._transfer_win.add_transfer(
            f"dl_{music_id}", filename, "DOWNLOAD",
            cancel_cb=self._tm.cancel_transfer,
        )
        self._tm.request_download(music_id, filename)
        self._status(f"download requested: {filename}")

    # ─── Publish ─────────────────────────────────────────────────────────────

    def _on_publish(self, metadata: dict):
        self._publish_win.set_busy(True)
        local_path = metadata.pop("local_path", "")
        future = self._tm.submit_api(self._api.publish_song(metadata))
        self._status("publishing...")

        def _check():
            if future.done():
                timer.stop()
                self._publish_win.set_busy(False)
                try:
                    result  = future.result()
                    music_id = result.get("music_id", "")
                    self._publish_win.set_status(
                        f"✓ published — id: {music_id}"
                    )
                    self._status(f"published: {metadata.get('filename')} (id={music_id})")
                except APIError as e:
                    self._publish_win.set_status(str(e), error=True)
                    self._status(str(e), error=True)

        timer = QTimer(self)
        timer.timeout.connect(_check)
        timer.start(300)

    # ─── History ─────────────────────────────────────────────────────────────

    def _on_refresh_history(self):
        future = self._tm.submit_api(self._api.get_history())
        self._status("loading history...")

        def _check():
            if future.done():
                timer.stop()
                try:
                    data = future.result()
                    self._history_win.populate(data)
                    self._status("history loaded")
                except APIError as e:
                    self._history_win.set_status(str(e), error=True)

        timer = QTimer(self)
        timer.timeout.connect(_check)
        timer.start(300)

    # ─── Peers ───────────────────────────────────────────────────────────────

    def _on_refresh_peers(self):
        future = self._tm.submit_api(self._api.get_peers())
        self._status("fetching peers...")

        def _check():
            if future.done():
                timer.stop()
                try:
                    data  = future.result()
                    peers = data.get("peers", []) if isinstance(data, dict) else data
                    self._peer_win.populate(peers)
                    self._status("peers updated")
                except APIError as e:
                    self._peer_win.set_status(str(e), error=True)

        timer = QTimer(self)
        timer.timeout.connect(_check)
        timer.start(300)

    # ─── Transfer callbacks ───────────────────────────────────────────────────

    def _on_transfer_done(self, transfer_id: str, path: str):
        self._transfer_win.on_done(transfer_id, path)
        self._status(f"transfer complete: {path}")

    def _on_transfer_failed(self, transfer_id: str, reason: str):
        self._transfer_win.on_failed(transfer_id, reason)
        self._status(f"transfer failed: {reason}", error=True)

    # ─── Status bar helper ────────────────────────────────────────────────────

    def _status(self, msg: str, error: bool = False):
        style = "color: #FF4757;" if error else ""
        self._statusbar.setStyleSheet(style)
        self._statusbar.showMessage(msg)

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._tm.stop()
        super().closeEvent(event)
