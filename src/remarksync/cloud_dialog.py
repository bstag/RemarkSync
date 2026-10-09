"""A single-document trial UI; network/PDF work stays in a child process."""
import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .cloud import CloudClient, PAIR_URL

DEFAULT_DESTINATION = Path(__file__).resolve().parents[2] / "output" / "cloud-trial"


class CloudTrialDialog(QDialog):
    def __init__(self, parent=None, destination: Path = DEFAULT_DESTINATION):
        super().__init__(parent)
        self.setWindowTitle("RemarkSync — read-only cloud trial")
        self.resize(660, 420)
        self.destination = Path(destination).resolve()
        self.process = None
        self.result_path = None
        layout = QVBoxLayout(self)
        intro = QLabel("Your tablet syncs to reMarkable Cloud over Wi-Fi. This trial downloads one PDF/EPUB and "
                       "preserves its notes in a separate PDF. It does not change cloud documents.\n\n"
                       "Sign in to reMarkable, generate a one-time device code, and enter it below.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.login = QPushButton("Open reMarkable pairing page")
        self.login.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(PAIR_URL)))
        layout.addWidget(self.login)
        self.code = QLineEdit()
        self.code.setEchoMode(QLineEdit.Password)
        self.code.setMaxLength(8)
        self.code.setPlaceholderText("Eight-character pairing code (stored tokens are protected by Windows)")
        layout.addWidget(self.code)
        self.pair = QPushButton("Pair account and list documents")
        self.pair.clicked.connect(self.pair_account)
        layout.addWidget(self.pair)
        self.refresh = QPushButton("Refresh cloud document list")
        self.refresh.clicked.connect(lambda: self.start({"operation": "list"}))
        layout.addWidget(self.refresh)
        self.documents = QComboBox()
        layout.addWidget(self.documents)
        self.download = QPushButton("Download selected document and preserve notes")
        self.download.clicked.connect(self.download_selected)
        self.download.setEnabled(False)
        layout.addWidget(self.download)
        self.status = QLabel("Ready to pair. Existing USB backups are kept separate.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        self.open_pdf = QPushButton("Open preservation PDF in your default reader")
        self.open_pdf.setEnabled(False)
        self.open_pdf.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.result_path)))
        layout.addWidget(self.open_pdf)
        location = QLabel(f"Trial archives: {self.destination}")
        location.setWordWrap(True)
        layout.addWidget(location)
        if CloudClient().paired:
            self.start({"operation": "list"})

    def pair_account(self):
        code = self.code.text().strip()
        self.code.clear()
        self.start({"operation": "pair", "code": code})

    def download_selected(self):
        identifier = self.documents.currentData()
        if identifier:
            self.start({"operation": "trial", "document": identifier, "destination": str(self.destination)})

    def start(self, request):
        if self.process is not None:
            return
        for widget in (self.pair, self.refresh, self.download, self.code, self.documents):
            widget.setEnabled(False)
        self.status.setText("Downloading and exporting…" if request["operation"] == "trial" else "Reading your cloud library…")
        process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONPATH", os.pathsep.join(str(path) for path in sys.path if path))
        process.setProcessEnvironment(env)
        process.finished.connect(lambda code, _status: self._operation_finished(process, code))
        process.errorOccurred.connect(lambda error: self._operation_finished(process, 1) if error == QProcess.FailedToStart else None)
        self.process = process
        process.start(sys.executable, ["-m", "remarksync.cloud_worker"])
        process.write(json.dumps(request).encode("utf-8"))
        process.closeWriteChannel()

    def _operation_finished(self, process, code):
        if self.process is not process:
            return
        try:
            result = json.loads(bytes(process.readAllStandardOutput()).decode("utf-8"))
        except (ValueError, UnicodeError):
            result = {"error": "Cloud worker stopped unexpectedly. Existing archives were retained."}
        self.process = None
        process.deleteLater()
        for widget in (self.pair, self.refresh, self.code, self.documents):
            widget.setEnabled(True)
        if code or "error" in result:
            self.status.setText(result.get("error", "Cloud operation failed."))
        elif "documents" in result:
            self.documents.clear()
            for doc in result["documents"]:
                if doc["file_type"] in ("pdf", "epub"):
                    self.documents.addItem(doc["name"], doc["id"])
            self.status.setText(f"Account paired. Found {self.documents.count()} PDF/EPUB documents. Choose one for the trial.")
        else:
            self.result_path = result["path"]
            self.open_pdf.setEnabled(True)
            warnings = result.get("warnings", [])
            message = f"Preserved {result['name']}: {result['pages']} pages. Raw archive and checksums saved."
            if warnings:
                message += f" {len(warnings)} fidelity warnings; first: {warnings[0]}"
            self.status.setText(message)
        self.download.setEnabled(self.documents.count() > 0)

    def closeEvent(self, event):
        if self.process is not None:
            self.status.setText("Wait for the cloud operation to finish before closing.")
            event.ignore()
        else:
            super().closeEvent(event)


def run(destination: Path = DEFAULT_DESTINATION):
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = CloudTrialDialog(destination=destination)
    dialog.show()
    app.exec()
