"""
ui/search_window.py
Search songs published on the server and initiate downloads.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)


class SearchWindow(QWidget):
    """
    Tab widget for searching and downloading songs.
    Emits search_requested(query) and download_requested(music_id, filename).
    """

    search_requested   = Signal(str)
    download_requested = Signal(str, str)   # music_id, filename

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: list[dict] = []
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 24, 32, 24)
        lay.setSpacing(12)

        sec = QLabel("SEARCH SONGS")
        sec.setObjectName("section")
        lay.addWidget(sec)

        # Search bar
        search_row = QHBoxLayout()
        self._txt_query = QLineEdit()
        self._txt_query.setPlaceholderText("search by title or artist...")
        self._txt_query.returnPressed.connect(self._on_search)
        btn_search = QPushButton("SEARCH")
        btn_search.setObjectName("primary")
        btn_search.setFixedWidth(100)
        btn_search.clicked.connect(self._on_search)
        search_row.addWidget(self._txt_query)
        search_row.addWidget(btn_search)
        lay.addLayout(search_row)

        # Results table
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["ID", "FILENAME", "SIZE", "OWNER", "MIME"]
        )
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        lay.addWidget(self._table)

        # Status + download button
        bottom_row = QHBoxLayout()
        self._lbl_status = QLabel("enter a query to search")
        self._lbl_status.setObjectName("subtitle")
        bottom_row.addWidget(self._lbl_status)
        bottom_row.addStretch()

        self._btn_download = QPushButton("⬇  DOWNLOAD SELECTED")
        self._btn_download.setObjectName("primary")
        self._btn_download.setEnabled(False)
        self._btn_download.clicked.connect(self._on_download)
        bottom_row.addWidget(self._btn_download)
        lay.addLayout(bottom_row)

        self._table.itemSelectionChanged.connect(self._on_selection_change)

    # ─── Handlers ────────────────────────────────────────────────────────────

    def _on_search(self):
        q = self._txt_query.text().strip()
        self._lbl_status.setText("searching...")
        self.search_requested.emit(q)

    def _on_selection_change(self):
        has_sel = bool(self._table.selectedItems())
        self._btn_download.setEnabled(has_sel)

    def _on_download(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._results):
            return
        song = self._results[row]
        self.download_requested.emit(
            song.get("music_id", song.get("id", "")),
            song.get("filename", "song"),
        )

    # ─── Public API ──────────────────────────────────────────────────────────

    def populate(self, songs: list[dict]):
        self._results = songs
        self._table.setRowCount(0)
        for song in songs:
            row = self._table.rowCount()
            self._table.insertRow(row)
            size_bytes = song.get("size", 0)
            size_str   = f"{size_bytes / 1024 / 1024:.1f} MB" if size_bytes else "—"
            for col, val in enumerate([
                str(song.get("music_id", song.get("id", ""))),
                song.get("filename", ""),
                size_str,
                song.get("owner", ""),
                song.get("mime_type", ""),
            ]):
                item = QTableWidgetItem(val)
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                self._table.setItem(row, col, item)

        self._lbl_status.setText(f"{len(songs)} result(s) found")

    def set_status(self, msg: str, error: bool = False):
        style = "color: #FF4757;" if error else "color: #555555;"
        self._lbl_status.setStyleSheet(style)
        self._lbl_status.setText(msg)
