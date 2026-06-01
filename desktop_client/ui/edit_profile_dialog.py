"""
ui/edit_profile_dialog.py
Profile editing and account deletion dialogs.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit,
    QTextEdit, QPushButton, QMessageBox
)


class EditProfileDialog(QDialog):
    def __init__(
        self,
        username: str = "",
        display_name: str = "",
        bio: str = "",
        parent=None,
    ):
        super().__init__(parent)

        self.setWindowTitle("Edit Profile")
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        self._lbl_username = QLabel(f"Username: {username}")
        lay.addWidget(self._lbl_username)

        lay.addWidget(QLabel("Display Name"))
        self._txt_display_name = QLineEdit()
        self._txt_display_name.setText(display_name or "")
        lay.addWidget(self._txt_display_name)

        lay.addWidget(QLabel("Bio"))
        self._txt_bio = QTextEdit()
        self._txt_bio.setPlainText(bio or "")
        self._txt_bio.setFixedHeight(90)
        lay.addWidget(self._txt_bio)

        lay.addWidget(QLabel("New Password (optional)"))
        self._txt_password = QLineEdit()
        self._txt_password.setEchoMode(QLineEdit.Password)
        self._txt_password.setPlaceholderText("leave empty if unchanged")
        lay.addWidget(self._txt_password)

        lay.addWidget(QLabel("Confirm New Password"))
        self._txt_confirm = QLineEdit()
        self._txt_confirm.setEchoMode(QLineEdit.Password)
        self._txt_confirm.setPlaceholderText("repeat new password")
        lay.addWidget(self._txt_confirm)

        self._btn_save = QPushButton("SAVE PROFILE")
        self._btn_save.clicked.connect(self._validate_and_accept)
        lay.addWidget(self._btn_save)

    def _validate_and_accept(self):
        password = self._txt_password.text()
        confirm = self._txt_confirm.text()

        if password or confirm:
            if password != confirm:
                QMessageBox.warning(
                    self,
                    "Password mismatch",
                    "Password confirmation does not match."
                )
                return

            if len(password) < 6:
                QMessageBox.warning(
                    self,
                    "Invalid password",
                    "Password must be at least 6 characters."
                )
                return

        self.accept()

    def data(self) -> dict:
        return {
            "display_name": self._txt_display_name.text().strip(),
            "bio": self._txt_bio.toPlainText().strip(),
            "password": self._txt_password.text().strip(),
        }


class DeleteProfileDialog(QDialog):
    def __init__(self, username: str = "", parent=None):
        super().__init__(parent)

        self.setWindowTitle("Delete Account")
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        warning = QLabel(
            f"Delete account '{username}'?\n\n"
            "This action cannot be undone.\n"
            "Your profile, sessions, peer registry, and published songs "
            "will be removed from the server database."
        )
        warning.setWordWrap(True)
        lay.addWidget(warning)

        lay.addWidget(QLabel("Enter your password to confirm"))
        self._txt_password = QLineEdit()
        self._txt_password.setEchoMode(QLineEdit.Password)
        lay.addWidget(self._txt_password)

        self._btn_delete = QPushButton("DELETE ACCOUNT")
        self._btn_delete.clicked.connect(self._validate_and_accept)
        lay.addWidget(self._btn_delete)

    def _validate_and_accept(self):
        if not self._txt_password.text().strip():
            QMessageBox.warning(
                self,
                "Password required",
                "Please enter your password."
            )
            return

        self.accept()

    def password(self) -> str:
        return self._txt_password.text().strip()
