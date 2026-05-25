"""
ui/login_window.py
Login / Register window.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QStackedWidget, QWidget, QFrame,
)


class LoginWindow(QDialog):
    """
    Modal dialog shown before main window.
    Emits login_success(username) or register_success() signals.
    """

    login_requested    = Signal(str, str)   # username, password
    register_requested = Signal(str, str)   # username, password

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("STP Music — Sign In")
        self.setFixedSize(400, 480)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self._build_ui()

    # ─── UI ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header strip
        header = QWidget()
        header.setFixedHeight(60)
        header.setStyleSheet("background-color: #0A0A0A; border-bottom: 1px solid #1E1E1E;")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(24, 0, 24, 0)
        lbl_title = QLabel("◈ STP MUSIC")
        lbl_title.setObjectName("title")
        lbl_title.setStyleSheet("font-size: 16px; letter-spacing: 4px; color: #00E5CC;")
        h_lay.addWidget(lbl_title)
        h_lay.addStretch()
        root.addWidget(header)

        # Body
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(40, 32, 40, 32)
        body_lay.setSpacing(0)

        # Stack: login page / register page
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_login_page())
        self._stack.addWidget(self._build_register_page())
        body_lay.addWidget(self._stack)

        # Status label
        self._lbl_status = QLabel("")
        self._lbl_status.setObjectName("status_err")
        self._lbl_status.setAlignment(Qt.AlignCenter)
        self._lbl_status.setWordWrap(True)
        body_lay.addSpacing(8)
        body_lay.addWidget(self._lbl_status)

        root.addWidget(body)

    def _build_login_page(self) -> QWidget:
        page = QWidget()
        lay  = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        lbl = QLabel("SIGN IN")
        lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #E0E0E0; letter-spacing: 3px;")
        lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(lbl)

        sub = QLabel("enter your credentials")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(24)

        self._login_user = QLineEdit()
        self._login_user.setPlaceholderText("username")
        lay.addWidget(self._login_user)

        self._login_pass = QLineEdit()
        self._login_pass.setPlaceholderText("password")
        self._login_pass.setEchoMode(QLineEdit.Password)
        lay.addWidget(self._login_pass)
        lay.addSpacing(8)

        btn_login = QPushButton("SIGN IN")
        btn_login.setObjectName("primary")
        btn_login.setFixedHeight(40)
        btn_login.clicked.connect(self._on_login)
        self._login_pass.returnPressed.connect(self._on_login)
        lay.addWidget(btn_login)

        lay.addSpacing(16)
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #1E1E1E;")
        lay.addWidget(sep)
        lay.addSpacing(16)

        lbl_reg = QLabel("don't have an account?")
        lbl_reg.setObjectName("subtitle")
        lbl_reg.setAlignment(Qt.AlignCenter)
        lay.addWidget(lbl_reg)

        btn_goto_reg = QPushButton("CREATE ACCOUNT")
        btn_goto_reg.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        lay.addWidget(btn_goto_reg)
        lay.addStretch()
        return page

    def _build_register_page(self) -> QWidget:
        page = QWidget()
        lay  = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        lbl = QLabel("CREATE ACCOUNT")
        lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #E0E0E0; letter-spacing: 3px;")
        lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(lbl)

        sub = QLabel("new user registration")
        sub.setObjectName("subtitle")
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(24)

        self._reg_user = QLineEdit()
        self._reg_user.setPlaceholderText("username")
        lay.addWidget(self._reg_user)

        self._reg_pass = QLineEdit()
        self._reg_pass.setPlaceholderText("password")
        self._reg_pass.setEchoMode(QLineEdit.Password)
        lay.addWidget(self._reg_pass)

        self._reg_pass2 = QLineEdit()
        self._reg_pass2.setPlaceholderText("confirm password")
        self._reg_pass2.setEchoMode(QLineEdit.Password)
        lay.addWidget(self._reg_pass2)
        lay.addSpacing(8)

        btn_reg = QPushButton("REGISTER")
        btn_reg.setObjectName("primary")
        btn_reg.setFixedHeight(40)
        btn_reg.clicked.connect(self._on_register)
        lay.addWidget(btn_reg)

        lay.addSpacing(16)
        btn_back = QPushButton("← BACK TO SIGN IN")
        btn_back.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        lay.addWidget(btn_back)
        lay.addStretch()
        return page

    # ─── Handlers ────────────────────────────────────────────────────────────

    def _on_login(self):
        u = self._login_user.text().strip()
        p = self._login_pass.text()
        if not u or not p:
            self.show_error("Username and password required.")
            return
        self.login_requested.emit(u, p)

    def _on_register(self):
        u  = self._reg_user.text().strip()
        p  = self._reg_pass.text()
        p2 = self._reg_pass2.text()
        if not u or not p:
            self.show_error("Fill in all fields.")
            return
        if p != p2:
            self.show_error("Passwords do not match.")
            return
        self.register_requested.emit(u, p)

    # ─── Public API ──────────────────────────────────────────────────────────

    def show_error(self, msg: str):
        self._lbl_status.setObjectName("status_err")
        self._lbl_status.setStyleSheet("color: #FF4757; font-size: 11px;")
        self._lbl_status.setText(msg)

    def show_info(self, msg: str):
        self._lbl_status.setStyleSheet("color: #00E5CC; font-size: 11px;")
        self._lbl_status.setText(msg)

    def set_busy(self, busy: bool):
        for w in self.findChildren(QPushButton):
            w.setEnabled(not busy)
