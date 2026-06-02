"""
ui/transfer_requests_panel.py

Owner-side panel for incoming download requests.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame,
)


class TransferRequestRow(QWidget):
    def __init__(self, request: dict, parent=None):
        super().__init__(parent)
        self.request = request
        self.request_id = str(request.get("request_id", ""))
        self.music_id = str(request.get("music_id", ""))
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 10)
        lay.setSpacing(6)

        title = (
            self.request.get("title")
            or self.request.get("song_title")
            or self.request.get("filename")
            or "Unknown song"
        )
        artist = self.request.get("artist", "")
        filename = self.request.get("filename", "")
        requester = (
            self.request.get("requester_name")
            or self.request.get("requester_username")
            or self.request.get("requester_id")
            or "unknown peer"
        )
        created_at = self.request.get("created_at", "")

        lbl_title = QLabel(f"⬇  {title}" + (f" — {artist}" if artist else ""))
        lbl_title.setStyleSheet("font-size: 13px; color: #C8C8C8;")
        lay.addWidget(lbl_title)

        detail = f"From: {requester}"
        if created_at:
            detail += f"  •  {created_at}"
        if filename:
            detail += f"\nFile: {filename}"
        detail += f"\nID: {self.request_id}"

        lbl_detail = QLabel(detail)
        lbl_detail.setObjectName("subtitle")
        lbl_detail.setWordWrap(True)
        lay.addWidget(lbl_detail)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_approve = QPushButton("APPROVE ✓")
        self.btn_approve.setObjectName("primary")
        self.btn_approve.setFixedWidth(110)

        self.btn_reject = QPushButton("REJECT ✗")
        self.btn_reject.setObjectName("danger")
        self.btn_reject.setFixedWidth(95)

        btn_row.addWidget(self.btn_approve)
        btn_row.addWidget(self.btn_reject)
        lay.addLayout(btn_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #1A1A1A;")
        lay.addWidget(sep)


class TransferRequestsPanel(QWidget):
    approve_requested = Signal(str, str)  # request_id, music_id
    reject_requested = Signal(str)        # request_id
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: dict[str, TransferRequestRow] = {}
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 24, 32, 24)
        lay.setSpacing(12)

        header = QHBoxLayout()

        sec = QLabel("DOWNLOAD REQUESTS FROM PEERS")
        sec.setObjectName("section")
        header.addWidget(sec)
        header.addStretch()

        self._btn_refresh = QPushButton("REFRESH")
        self._btn_refresh.setFixedWidth(100)
        self._btn_refresh.clicked.connect(self.refresh_requested.emit)
        header.addWidget(self._btn_refresh)

        lay.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        self._container = QWidget()
        self._container_lay = QVBoxLayout(self._container)
        self._container_lay.setContentsMargins(0, 0, 0, 0)
        self._container_lay.setSpacing(0)
        self._container_lay.addStretch()

        scroll.setWidget(self._container)
        lay.addWidget(scroll)

        self._lbl_empty = QLabel("no pending transfer requests")
        self._lbl_empty.setObjectName("subtitle")
        lay.addWidget(self._lbl_empty)

    def populate(self, requests: list[dict]):
        for request in requests:
            self.add_request(request)

    def add_request(self, request: dict):
        request_id = str(request.get("request_id", ""))
        if not request_id or request_id in self._rows:
            return

        row = TransferRequestRow(request)
        row.btn_approve.clicked.connect(
            lambda checked=False, r=row: self.approve_requested.emit(r.request_id, r.music_id)
        )
        row.btn_reject.clicked.connect(
            lambda checked=False, r=row: self.reject_requested.emit(r.request_id)
        )

        count = self._container_lay.count()
        self._container_lay.insertWidget(count - 1, row)
        self._rows[request_id] = row
        self._lbl_empty.setVisible(False)

    def remove_request(self, request_id: str):
        row = self._rows.pop(request_id, None)
        if row:
            row.setParent(None)
            row.deleteLater()
        self._lbl_empty.setVisible(not self._rows)

    def clear(self):
        for request_id in list(self._rows.keys()):
            self.remove_request(request_id)
