"""
ui/history_window.py
Displays login, publish, and download history fetched from server.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QTabWidget,
)


def _make_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setStretchLastSection(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.setShowGrid(False)
    return t


def _fill(table: QTableWidget, rows: list[list[str]]):
    table.setRowCount(0)
    for cols in rows:
        r = table.rowCount()
        table.insertRow(r)
        for c, val in enumerate(cols):
            item = QTableWidgetItem(str(val))
            item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            table.setItem(r, c, item)


class HistoryWindow(QWidget):
    """History viewer tab."""

    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 24, 32, 24)
        lay.setSpacing(12)

        top = QHBoxLayout()
        sec = QLabel("HISTORY")
        sec.setObjectName("section")
        top.addWidget(sec)
        top.addStretch()
        btn_refresh = QPushButton("REFRESH")
        btn_refresh.setFixedWidth(100)
        btn_refresh.clicked.connect(self.refresh_requested.emit)
        top.addWidget(btn_refresh)
        lay.addLayout(top)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet("QTabBar::tab { padding: 6px 16px; font-size: 11px; }")

        # Download history
        self._tbl_download = _make_table(
            ["MUSIC ID", "FILENAME", "FROM PEER", "TIMESTAMP"]
        )
        self._tabs.addTab(self._tbl_download, "DOWNLOADS")

        # Publish history
        self._tbl_publish = _make_table(
            ["MUSIC ID", "FILENAME", "TIMESTAMP"]
        )
        self._tabs.addTab(self._tbl_publish, "PUBLISHES")

        # Login history
        self._tbl_login = _make_table(
            ["EVENT", "IP", "TIMESTAMP"]
        )
        self._tabs.addTab(self._tbl_login, "LOGINS")

        lay.addWidget(self._tabs)

        self._lbl_status = QLabel("")
        self._lbl_status.setObjectName("subtitle")
        lay.addWidget(self._lbl_status)

    # ─── Public API ──────────────────────────────────────────────────────────

    def populate(self, data: dict):
        downloads = data.get("downloads", [])
        publishes = data.get("publishes", [])
        logins    = data.get("logins", [])

        _fill(self._tbl_download, [
            [
                str(d.get("music_id", "")),
                d.get("filename", ""),
                d.get("from_peer", ""),
                d.get("timestamp", ""),
            ]
            for d in downloads
        ])

        _fill(self._tbl_publish, [
            [
                str(p.get("music_id", "")),
                p.get("filename", ""),
                p.get("timestamp", ""),
            ]
            for p in publishes
        ])

        _fill(self._tbl_login, [
            [
                l.get("event", ""),
                l.get("ip", ""),
                l.get("timestamp", ""),
            ]
            for l in logins
        ])

        total = len(downloads) + len(publishes) + len(logins)
        self._lbl_status.setText(f"loaded {total} records")

    def set_status(self, msg: str, error: bool = False):
        style = "color: #FF4757;" if error else "color: #555555;"
        self._lbl_status.setStyleSheet(style)
        self._lbl_status.setText(msg)
