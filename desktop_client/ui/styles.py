"""
ui/styles.py
Global QSS stylesheet — dark industrial theme with teal accent.
"""

STYLESHEET = """
/* ── Root ─────────────────────────────────────────────────────────────── */
* {
    font-family: "Courier New", monospace;
    color: #E0E0E0;
    background-color: transparent;
}

QMainWindow, QDialog {
    background-color: #0F0F0F;
}

QWidget {
    background-color: #0F0F0F;
}

/* ── Typography ───────────────────────────────────────────────────────── */
QLabel {
    font-size: 13px;
    color: #C8C8C8;
    background: transparent;
}

QLabel#title {
    font-size: 22px;
    font-weight: bold;
    color: #00E5CC;
    letter-spacing: 3px;
}

QLabel#subtitle {
    font-size: 11px;
    color: #555555;
    letter-spacing: 2px;
}

QLabel#section {
    font-size: 11px;
    font-weight: bold;
    color: #00E5CC;
    letter-spacing: 2px;
    padding: 4px 0;
    border-bottom: 1px solid #1E1E1E;
}

QLabel#status_ok {
    color: #00E5CC;
    font-size: 11px;
}

QLabel#status_err {
    color: #FF4757;
    font-size: 11px;
}

/* ── Inputs ───────────────────────────────────────────────────────────── */
QLineEdit {
    background-color: #151515;
    border: 1px solid #2A2A2A;
    border-radius: 2px;
    padding: 6px 10px;
    font-size: 13px;
    color: #E0E0E0;
    selection-background-color: #00E5CC;
    selection-color: #000000;
}

QLineEdit:focus {
    border: 1px solid #00E5CC;
    background-color: #1A1A1A;
}

QLineEdit:disabled {
    color: #444444;
    border-color: #1E1E1E;
}

/* ── Buttons ──────────────────────────────────────────────────────────── */
QPushButton {
    background-color: #1A1A1A;
    border: 1px solid #333333;
    border-radius: 2px;
    padding: 7px 18px;
    font-size: 12px;
    font-weight: bold;
    color: #C8C8C8;
    letter-spacing: 1px;
}

QPushButton:hover {
    background-color: #222222;
    border-color: #00E5CC;
    color: #00E5CC;
}

QPushButton:pressed {
    background-color: #00E5CC;
    color: #000000;
}

QPushButton:disabled {
    color: #3A3A3A;
    border-color: #1E1E1E;
    background-color: #111111;
}

QPushButton#primary {
    background-color: #00E5CC;
    color: #000000;
    border: none;
}

QPushButton#primary:hover {
    background-color: #00FFE0;
    color: #000000;
}

QPushButton#primary:pressed {
    background-color: #00B8A0;
}

QPushButton#danger {
    border-color: #FF4757;
    color: #FF4757;
}

QPushButton#danger:hover {
    background-color: #FF4757;
    color: #000000;
}

/* ── Tables ───────────────────────────────────────────────────────────── */
QTableWidget {
    background-color: #111111;
    alternate-background-color: #141414;
    border: 1px solid #1E1E1E;
    gridline-color: #1A1A1A;
    font-size: 12px;
    selection-background-color: #003D35;
    selection-color: #00E5CC;
}

QTableWidget::item {
    padding: 4px 8px;
    border: none;
}

QHeaderView::section {
    background-color: #0A0A0A;
    color: #00E5CC;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 2px;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid #1E1E1E;
    border-bottom: 1px solid #1E1E1E;
}

/* ── Progress bar ─────────────────────────────────────────────────────── */
QProgressBar {
    background-color: #151515;
    border: 1px solid #222222;
    border-radius: 1px;
    height: 6px;
    text-align: center;
    font-size: 10px;
    color: transparent;
}

QProgressBar::chunk {
    background-color: #00E5CC;
    border-radius: 1px;
}

/* ── Tab bar ──────────────────────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #1E1E1E;
    background-color: #0F0F0F;
}

QTabBar::tab {
    background-color: #111111;
    color: #555555;
    padding: 8px 20px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 11px;
    letter-spacing: 1px;
}

QTabBar::tab:selected {
    color: #00E5CC;
    border-bottom: 2px solid #00E5CC;
    background-color: #0F0F0F;
}

QTabBar::tab:hover {
    color: #C8C8C8;
}

/* ── Scroll bar ───────────────────────────────────────────────────────── */
QScrollBar:vertical {
    background-color: #0F0F0F;
    width: 8px;
    border: none;
}

QScrollBar::handle:vertical {
    background-color: #2A2A2A;
    border-radius: 4px;
    min-height: 20px;
}

QScrollBar::handle:vertical:hover {
    background-color: #00E5CC;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background-color: #0F0F0F;
    height: 8px;
    border: none;
}

QScrollBar::handle:horizontal {
    background-color: #2A2A2A;
    border-radius: 4px;
}

/* ── Combobox ─────────────────────────────────────────────────────────── */
QComboBox {
    background-color: #151515;
    border: 1px solid #2A2A2A;
    border-radius: 2px;
    padding: 6px 10px;
    font-size: 13px;
    color: #E0E0E0;
}

QComboBox:focus {
    border-color: #00E5CC;
}

QComboBox QAbstractItemView {
    background-color: #151515;
    border: 1px solid #2A2A2A;
    selection-background-color: #003D35;
    selection-color: #00E5CC;
}

/* ── Status bar ───────────────────────────────────────────────────────── */
QStatusBar {
    background-color: #0A0A0A;
    color: #444444;
    font-size: 11px;
    border-top: 1px solid #1A1A1A;
}

/* ── Separators ───────────────────────────────────────────────────────── */
QFrame[frameShape="4"], QFrame[frameShape="5"] {
    color: #1E1E1E;
}

/* ── Sidebar nav buttons ──────────────────────────────────────────────── */
QPushButton#nav {
    background-color: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0;
    text-align: left;
    padding: 10px 16px;
    font-size: 12px;
    letter-spacing: 1px;
    color: #555555;
}

QPushButton#nav:hover {
    color: #C8C8C8;
    background-color: #151515;
    border-left-color: #333333;
}

QPushButton#nav:checked {
    color: #00E5CC;
    background-color: #111111;
    border-left-color: #00E5CC;
}

/* ── Message / Toast ──────────────────────────────────────────────────── */
QLabel#toast {
    background-color: #1A1A1A;
    border: 1px solid #00E5CC;
    border-radius: 2px;
    padding: 6px 12px;
    font-size: 11px;
    color: #00E5CC;
}

QLabel#toast_err {
    background-color: #1A0000;
    border: 1px solid #FF4757;
    border-radius: 2px;
    padding: 6px 12px;
    font-size: 11px;
    color: #FF4757;
}
"""
