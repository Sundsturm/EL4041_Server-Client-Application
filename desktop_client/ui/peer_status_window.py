"""
ui/peer_status_window.py
Displays online peers from server registry.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)


class PeerStatusWindow(QWidget):
    """Peer status tab."""

    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 24, 32, 24)
        lay.setSpacing(12)

        top = QHBoxLayout()
        sec = QLabel("PEER STATUS")
        sec.setObjectName("section")
        top.addWidget(sec)
        top.addStretch()

        self._lbl_count = QLabel("0 peers online")
        self._lbl_count.setObjectName("subtitle")
        top.addWidget(self._lbl_count)

        btn_refresh = QPushButton("REFRESH")
        btn_refresh.setFixedWidth(100)
        btn_refresh.clicked.connect(self.refresh_requested.emit)
        top.addWidget(btn_refresh)
        lay.addLayout(top)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["PEER ID", "IP", "PORT", "STATUS", "LAST SEEN"]
        )
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        lay.addWidget(self._table)

        self._lbl_status = QLabel("")
        self._lbl_status.setObjectName("subtitle")
        lay.addWidget(self._lbl_status)

    # ─── Public API ──────────────────────────────────────────────────────────

    def populate(self, peers: list[dict]):
        self._table.setRowCount(0)
        for peer in peers:
            r = self._table.rowCount()
            self._table.insertRow(r)

            status = peer.get("status", "unknown")
            for col, val in enumerate([
                str(peer.get("peer_id", "")),
                peer.get("ip", ""),
                str(peer.get("port", "")),
                status,
                peer.get("last_seen", ""),
            ]):
                item = QTableWidgetItem(val)
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                if col == 3:  # Status column colour
                    if status == "online":
                        item.setForeground(Qt.green)
                    else:
                        item.setForeground(Qt.darkGray)
                self._table.setItem(r, col, item)

        online = sum(1 for p in peers if p.get("status") == "online")
        self._lbl_count.setText(f"{online} / {len(peers)} peers online")

    def set_status(self, msg: str, error: bool = False):
        style = "color: #FF4757;" if error else "color: #555555;"
        self._lbl_status.setStyleSheet(style)
        self._lbl_status.setText(msg)
