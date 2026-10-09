"""Masked server pairing over the dedicated SSH channel; no code in argv/logs.

This installation's host, key and release path come from the environment:
REMARKSYNC_SSH_HOST (user@host), REMARKSYNC_RELEASE (the remote release
directory) and the optional REMARKSYNC_SSH_KEY. Nothing is built in.
"""
import json
import os
from pathlib import Path

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QPushButton, QVBoxLayout

ROOT = Path(__file__).resolve().parents[1]
HOST = os.environ.get("REMARKSYNC_SSH_HOST", "").strip()
RELEASE = os.environ.get("REMARKSYNC_RELEASE", "").strip()
KEY = os.environ.get("REMARKSYNC_SSH_KEY", "").strip() or str(ROOT / ".tools/deploy-ssh/id_ed25519")


class PairServer(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RemarkSync — Pair the preservation server")
        self.resize(480, 230)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Pair the Ubuntu server separately so it can sync while this PC is off."))
        page = QPushButton("Open reMarkable pairing page")
        page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://my.remarkable.com/device/browser/connect")))
        layout.addWidget(page)
        self.code = QLineEdit()
        self.code.setEchoMode(QLineEdit.EchoMode.Password)
        self.code.setMaxLength(8)
        self.code.setPlaceholderText("Fresh eight-character pairing code")
        layout.addWidget(self.code)
        self.button = QPushButton("Pair the server")
        self.button.clicked.connect(self.pair)
        layout.addWidget(self.button)
        self.status = QLabel("The code is sent through SSH stdin and is never saved here.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.process = QProcess(self)
        # QDialog already defines a finished signal; use a distinct slot name.
        self.process.finished.connect(self.on_process_finished)
        self.process.errorOccurred.connect(self.failed)

    def pair(self):
        code = self.code.text().strip()
        if len(code) != 8 or not code.isascii() or not code.isalnum():
            self.status.setText("Enter a fresh code containing eight letters/digits.")
            return
        if not HOST or not RELEASE:
            self.status.setText("Set REMARKSYNC_SSH_HOST and REMARKSYNC_RELEASE for your own deployment first.")
            return
        self.code.clear()
        self.code.setEnabled(False)
        self.button.setEnabled(False)
        self.status.setText("Pairing the server; this can take a minute…")
        command = (f"sudo -n docker compose -f {RELEASE}/deploy/server/compose.yaml run "
                   "--rm --no-deps -T --entrypoint python sync -m remarksync.cloud_worker")
        self.process.start("ssh", ["-T", "-i", KEY,
                                   "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                                   "-o", "ConnectTimeout=10", HOST, command])
        self.process.write(json.dumps({"operation": "pair", "code": code}).encode())
        self.process.closeWriteChannel()

    def on_process_finished(self, exit_code, _exit_status):
        try:
            result = json.loads(bytes(self.process.readAllStandardOutput()))
            success = exit_code == 0 and "documents" in result and "error" not in result
        except (ValueError, TypeError):
            result, success = {}, False
        # Never write codes, credentials, raw SSH output or document names to disk.
        result_path = ROOT / "tmp/deploy/server-pair-result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps({"paired": success}))
        self.status.setText("The server is paired. You can close this window."
                            if success else result.get("error", "Server pairing failed; check the code and SSH connection."))
        self.button.setEnabled(not success)
        self.code.setEnabled(not success)

    def failed(self, _error):
        self.status.setText("Could not start the SSH pairing process.")
        self.button.setEnabled(True)
        self.code.setEnabled(True)

    def closeEvent(self, event):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.status.setText("Wait for the pairing process to finish before closing.")
            event.ignore()
        else:
            event.accept()


if __name__ == "__main__":
    application = QApplication([])
    dialog = PairServer()
    dialog.show()
    application.exec()
