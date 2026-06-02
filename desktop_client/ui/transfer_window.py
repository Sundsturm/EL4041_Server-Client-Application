"""
ui/transfer_window.py
Live transfer queue: shows progress bars, status, cancel buttons.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QScrollArea, QFrame,
)


class TransferRow(QWidget):
    """Single row showing one transfer's progress."""

    def __init__(self, transfer_id: str, filename: str, direction: str, parent=None):
        super().__init__(parent)
        self.transfer_id = transfer_id
        self._build_ui(filename, direction)

    def _build_ui(self, filename: str, direction: str):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 8)
        lay.setSpacing(4)

        top = QHBoxLayout()
        arrow = "⬇" if direction == "DOWNLOAD" else "⬆"
        lbl_name = QLabel(f"{arrow}  {filename}")
        lbl_name.setStyleSheet("font-size: 13px; color: #C8C8C8;")
        top.addWidget(lbl_name)
        top.addStretch()

        self._lbl_pct = QLabel("0%")
        self._lbl_pct.setStyleSheet("color: #555555; font-size: 11px;")
        top.addWidget(self._lbl_pct)

        self._btn_cancel = QPushButton("CANCEL")
        self._btn_cancel.setObjectName("danger")
        self._btn_cancel.setFixedSize(70, 24)
        top.addWidget(self._btn_cancel)
        lay.addLayout(top)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFixedHeight(6)
        lay.addWidget(self._bar)

        self._lbl_status = QLabel("pending...")
        self._lbl_status.setObjectName("subtitle")
        lay.addWidget(self._lbl_status)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #1A1A1A;")
        lay.addWidget(sep)

    def update_progress(self, done: int, total: int):
        pct = int(done / total * 100) if total else 0
        self._bar.setValue(pct)
        self._lbl_pct.setText(f"{pct}%")
        self._lbl_status.setText(f"{done} / {total} chunks")

    def set_done(self, path: str = ""):
        self._bar.setValue(100)
        self._lbl_pct.setText("100%")
        self._lbl_status.setText(f"✓ done  {path}")
        self._lbl_status.setStyleSheet("color: #00E5CC; font-size: 11px;")
        self._btn_cancel.setEnabled(False)

    def set_failed(self, reason: str):
        self._lbl_status.setText(f"✗ {reason}")
        self._lbl_status.setStyleSheet("color: #FF4757; font-size: 11px;")
        self._btn_cancel.setEnabled(False)

    @property
    def cancel_button(self) -> QPushButton:
        return self._btn_cancel


class TransferWindow(QWidget):
    """
    Tab showing all active / completed transfers.
    Connected to TransferManager signals externally (in MainWindow).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: dict[str, TransferRow] = {}
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 24, 32, 24)
        lay.setSpacing(12)

        sec = QLabel("TRANSFER QUEUE")
        sec.setObjectName("section")
        lay.addWidget(sec)

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

        self._lbl_empty = QLabel("no active transfers")
        self._lbl_empty.setObjectName("subtitle")
        self._lbl_empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._lbl_empty)

    # ─── Public API ──────────────────────────────────────────────────────────

    def add_transfer(
        self,
        transfer_id: str,
        filename: str,
        direction: str = "DOWNLOAD",
        cancel_cb=None,
    ) -> TransferRow:
        row = TransferRow(transfer_id, filename, direction)
        if cancel_cb:
            row.cancel_button.clicked.connect(lambda: cancel_cb(transfer_id))
        # Insert before the trailing stretch
        count = self._container_lay.count()
        self._container_lay.insertWidget(count - 1, row)
        self._rows[transfer_id] = row
        self._lbl_empty.setVisible(False)
        return row

    def on_progress(self, transfer_id: str, done: int, total: int):
        if transfer_id in self._rows:
            self._rows[transfer_id].update_progress(done, total)

    def on_upload_progress(self, transfer_id: str, done: int, total: int):
        self.on_progress(transfer_id, done, total)

    def on_done(self, transfer_id: str, path: str):
        if transfer_id in self._rows:
            self._rows[transfer_id].set_done(path)

    def on_failed(self, transfer_id: str, reason: str):
        if transfer_id in self._rows:
            self._rows[transfer_id].set_failed(reason)
