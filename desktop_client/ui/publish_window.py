"""
ui/publish_window.py
Publish a song: pick file, compute metadata, POST to server.
"""

import hashlib
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFileDialog, QProgressBar,
    QFrame,
)

from config import SUPPORTED_MIME_TYPES, STP_LISTEN_PORT
from core.audio_metadata import extract_metadata


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


class PublishWindow(QWidget):
    """
    Tab widget for publishing a song.
    Emits publish_requested(metadata_dict) when user clicks Publish.
    """

    publish_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_path = ""
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 24, 32, 24)
        lay.setSpacing(16)

        # Section header
        sec = QLabel("PUBLISH SONG")
        sec.setObjectName("section")
        lay.addWidget(sec)

        # File picker
        file_row = QHBoxLayout()
        self._txt_file = QLineEdit()
        self._txt_file.setPlaceholderText("select audio file...")
        self._txt_file.setReadOnly(True)
        btn_browse = QPushButton("BROWSE")
        btn_browse.setFixedWidth(90)
        btn_browse.clicked.connect(self._browse)
        file_row.addWidget(self._txt_file)
        file_row.addWidget(btn_browse)
        lay.addLayout(file_row)

        # Metadata fields
        self._txt_title = QLineEdit()
        self._txt_title.setPlaceholderText("track title (optional)")

        self._txt_artist = QLineEdit()
        self._txt_artist.setPlaceholderText("artist (optional)")

        self._txt_album = QLineEdit()
        self._txt_album.setPlaceholderText("album (optional)")

        lay.addWidget(QLabel("Title"))
        lay.addWidget(self._txt_title)

        lay.addWidget(QLabel("Artist"))
        lay.addWidget(self._txt_artist)

        lay.addWidget(QLabel("Album"))
        lay.addWidget(self._txt_album)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #1E1E1E;")
        lay.addWidget(sep)

        # File info labels
        self._lbl_size = QLabel("size: —")
        self._lbl_hash = QLabel("sha256: —")
        self._lbl_mime = QLabel("mime: —")
        self._lbl_duration = QLabel("duration: —")
        for lbl in (self._lbl_size, self._lbl_hash, self._lbl_mime, self._lbl_duration):
            lbl.setObjectName("subtitle")
            lay.addWidget(lbl)

        lay.addSpacing(8)

        # Progress / status
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        lay.addWidget(self._progress)

        self._lbl_status = QLabel("")
        self._lbl_status.setObjectName("status_ok")
        lay.addWidget(self._lbl_status)

        lay.addStretch()

        # Publish button
        self._btn_publish = QPushButton("PUBLISH")
        self._btn_publish.setObjectName("primary")
        self._btn_publish.setFixedHeight(40)
        self._btn_publish.setEnabled(False)
        self._btn_publish.clicked.connect(self._on_publish)
        lay.addWidget(self._btn_publish)

    def _browse(self):
        exts = " ".join(f"*{e}" for e in SUPPORTED_MIME_TYPES)

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Audio File",
            "",
            f"Audio Files ({exts});;All Files (*)"
        )

        if not path:
            return

        self._file_path = path
        self._txt_file.setText(path)

        # ==========================
        # Read metadata from audio
        # ==========================
        meta = extract_metadata(path)

        # Title
        if meta.get("title"):
            self._txt_title.setText(meta["title"])
        else:
            base = os.path.splitext(os.path.basename(path))[0]
            self._txt_title.setText(base)

        # Artist
        if meta.get("artist"):
            self._txt_artist.setText(meta["artist"])

        if meta.get("album"):
            self._txt_album.setText(meta["album"])

        # ==========================
        # File info
        # ==========================
        size = os.path.getsize(path)

        ext = os.path.splitext(path)[1].lower()
        mime = SUPPORTED_MIME_TYPES.get(
            ext,
            "application/octet-stream"
        )

        self._lbl_size.setText(
            f"size: {size:,} bytes ({size / 1024 / 1024:.2f} MB)"
        )

        self._lbl_mime.setText(
            f"mime: {mime}"
        )

        # ==========================
        # Duration
        # ==========================
        duration = meta.get("duration", 0)

        minutes = duration // 60
        seconds = duration % 60

        self._lbl_duration.setText(
            f"duration: {minutes:02d}:{seconds:02d}"
        )

        # ==========================
        # SHA256 hash
        # ==========================
        self._lbl_hash.setText(
            "sha256: computing..."
        )

        self._btn_publish.setEnabled(False)

        self._progress.setVisible(True)

        self._hash_worker = _HashWorker(path)
        self._hash_worker.done.connect(
            self._on_hash_done
        )
        self._hash_worker.start()

    def _on_hash_done(self, digest: str):
        self._progress.setVisible(False)
        self._lbl_hash.setText(f"sha256: {digest}")
        self._btn_publish.setEnabled(True)

    def _on_publish(self):
        if not self._file_path:
            return
        ext  = os.path.splitext(self._file_path)[1].lower()
        mime = SUPPORTED_MIME_TYPES.get(ext, "application/octet-stream")
        metadata = {
            "filename":  os.path.basename(self._file_path),
            "mime_type": mime,
            "size":      os.path.getsize(self._file_path),
            "hmac_hash": self._lbl_hash.text().replace("sha256: ", ""),  # BUG 2 fix: was 'hash'
            "stp_port":  STP_LISTEN_PORT,   # BUG 3 fix: required by server PublishRequest
            "title": self._txt_title.text().strip(),
            "artist": self._txt_artist.text().strip(),
            "album": self._txt_album.text().strip(),
            "local_path": self._file_path,
        }
        self.publish_requested.emit(metadata)

    def set_status(self, msg: str, error: bool = False):
        style = "color: #FF4757;" if error else "color: #00E5CC;"
        self._lbl_status.setStyleSheet(style)
        self._lbl_status.setText(msg)

    def set_busy(self, busy: bool):
        self._btn_publish.setEnabled(not busy)
        self._progress.setVisible(busy)


# ─── Hash worker thread ───────────────────────────────────────────────────────

from PySide6.QtCore import QThread, Signal as QSignal


class _HashWorker(QThread):
    done = QSignal(str)

    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def run(self):
        digest = _sha256(self._path)
        self.done.emit(digest)
