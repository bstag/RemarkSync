"""Cloud polling controls. Network and MuPDF work run in a child process."""
from pathlib import Path
import sys

from PySide6.QtCore import QSettings, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout

from .cloud import CloudClient, PAIR_URL
from .cloudsync import DEFAULT_DESTINATION
from .serversync import DEFAULT_DESTINATION as SERVER_DESTINATION, default_server
from .workerprocess import start_json_worker


def sync_settings() -> QSettings:
    """Where the sync preferences live; the main window reads ``automatic`` from here too."""
    return QSettings("RemarkSync", "CloudSync")


class CloudSyncDialog(QDialog):
    library_requested = Signal(str)
    synced = Signal(str)

    def __init__(self, parent=None, destination: Path | None = None, *, automatic: bool | None = None,
                 settings=None, source: str | None = None, server: str | None = None):
        """``destination`` None means "not given": the saved folder for the
        chosen source is used. A path replaces that saved folder."""
        super().__init__(parent)
        self.setWindowTitle("RemarkSync — Cloud sync")
        self.resize(680, 460)
        self.settings = settings or sync_settings()
        chosen = source or self.settings.value("source", "cloud")
        self.destinations = {"cloud": Path(self.settings.value("destination", str(DEFAULT_DESTINATION))).resolve(),
                             "server": Path(self.settings.value("server_destination", str(SERVER_DESTINATION))).resolve()}
        if destination is not None:
            self.destinations[chosen] = Path(destination).resolve()
        self.destination = self.destinations[chosen]
        self.process = None
        self.failures = 0
        self.operation = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.sync_now)
        layout = QVBoxLayout(self)
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        layout.addWidget(self.intro)
        self.source = QComboBox()
        self.source.addItem("reMarkable Cloud directly", "cloud")
        self.source.addItem("Central preservation server", "server")
        self.source.setCurrentIndex(1 if chosen == "server" else 0)
        layout.addWidget(self.source)
        self.server_address = QLineEdit(server or self.settings.value("server_url", "") or default_server())
        self.server_address.setPlaceholderText("Private server HTTPS address, such as https://server.tailnet.ts.net:8443")
        self.server_address.setAccessibleName("Private server address")
        self.server_address.editingFinished.connect(lambda: self.settings.setValue("server_url", self.server_address.text().strip()))
        layout.addWidget(self.server_address)
        self.login = QPushButton("Open reMarkable pairing page")
        self.login.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(PAIR_URL)))
        layout.addWidget(self.login)
        self.code = QLineEdit()
        self.code.setEchoMode(QLineEdit.Password)
        self.code.setMaxLength(8)
        self.code.setPlaceholderText("One-time code (only needed for pairing/re-pairing)")
        layout.addWidget(self.code)
        self.pair = QPushButton("Pair account")
        self.pair.clicked.connect(self.pair_account)
        layout.addWidget(self.pair)
        self.pdfs_only = QCheckBox("Download PDFs and EPUBs only")
        self.pdfs_only.setChecked(self.settings.value("pdfs_only", False, type=bool))
        self.pdfs_only.toggled.connect(lambda checked: self.settings.setValue("pdfs_only", checked))
        layout.addWidget(self.pdfs_only)
        self.interval = QSpinBox()
        self.interval.setRange(1, 60)
        self.interval.setSuffix(" minutes between checks")
        self.interval.setValue(self.settings.value("interval_minutes", 5, type=int))
        self.interval.valueChanged.connect(self.interval_changed)
        layout.addWidget(self.interval)
        self.auto = QCheckBox("Sync automatically while RemarkSync is open")
        layout.addWidget(self.auto)
        self.sync_button = QPushButton("Sync now")
        self.sync_button.clicked.connect(self.sync_now)
        layout.addWidget(self.sync_button)
        self.open_library = QPushButton("Open downloaded library")
        self.open_library.clicked.connect(self.show_library)
        layout.addWidget(self.open_library)
        self.open_exports = QPushButton("Open preservation PDFs")
        self.open_exports.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.destination / "preservation.html"))))
        layout.addWidget(self.open_exports)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.location = QLabel()
        self.location.setWordWrap(True)
        layout.addWidget(self.location)
        self.source.currentIndexChanged.connect(self.source_changed)
        self.source_changed()
        self.auto.toggled.connect(self.auto_changed)
        self.auto.setChecked(self.settings.value("automatic", False, type=bool) if automatic is None else automatic)

    def source_changed(self, *_args):
        central = self.source.currentData() == "server"
        self.destination = self.destinations[self.source.currentData()]
        self.settings.setValue("source", self.source.currentData())
        self.settings.setValue("server_destination" if central else "destination", str(self.destination))
        for widget in (self.login, self.code, self.pair, self.pdfs_only):
            widget.setVisible(not central)
        self.server_address.setVisible(central)
        self.intro.setText(("Download the whole library and preserved PDFs from your central server over Tailscale. "
                            "Earlier revisions stay on this PC. The server handles reMarkable pairing and PDF exports."
                            if central else "Download your library directly from reMarkable Cloud. Earlier revisions stay "
                            "on this PC, and PDF annotations are preserved automatically.")
                           + "\n\nAutomatic checks run while RemarkSync is open on this PC.")
        self.status.setText("Ready to download from the private server. Connect Tailscale first." if central else
                            ("Account paired. Ready to sync." if CloudClient().paired else "Pair your account before syncing."))
        self.location.setText(f"Downloaded library and preservation revisions: {self.destination}")
        self._update_open_buttons()
        if self.auto.isChecked():
            self.timer.start(0)

    def _update_open_buttons(self):
        self.open_library.setEnabled((self.destination / "raw").is_dir())
        self.open_exports.setEnabled((self.destination / "preservation.html").is_file())

    def _set_controls_enabled(self, enabled):
        for widget in (self.pair, self.sync_button, self.code, self.pdfs_only, self.source, self.server_address):
            widget.setEnabled(enabled)

    def pair_account(self):
        code = self.code.text().strip()
        self.code.clear()
        self.start({"operation": "pair", "code": code})

    def interval_changed(self, value):
        self.settings.setValue("interval_minutes", value)
        if self.auto.isChecked() and self.process is None:
            self.schedule()

    def auto_changed(self, checked):
        self.settings.setValue("automatic", checked)
        self.timer.stop()
        if checked:
            self.timer.start(0)
        elif self.process is not None:
            self.status.setText("Automatic checks stopped. The current sync will finish safely.")

    def schedule(self):
        if self.auto.isChecked():
            self.timer.start(min(self.interval.value() * 60_000 * 2**self.failures, 3_600_000))

    def sync_now(self):
        if self.process is not None:
            return
        if self.source.currentData() == "server":
            address = self.server_address.text().strip()
            if not address:
                self.status.setText("Enter your private server's HTTPS address first.")
                return
            self.settings.setValue("server_url", address)
            self.start({"operation": "server-sync", "destination": str(self.destination), "server": address})
        else:
            self.start({"operation": "sync", "destination": str(self.destination), "pdfs_only": self.pdfs_only.isChecked()})

    def start(self, request):
        if self.process is not None:
            return
        self.timer.stop()
        self.operation = request["operation"]
        self._set_controls_enabled(False)
        self.open_library.setEnabled(False)
        self.open_exports.setEnabled(False)
        self.status.setText("Checking the central library…" if self.operation == "server-sync" else "Checking the cloud library…")
        self.process = start_json_worker(self, "remarksync.cloud_worker", request, self.finished_operation, self.progress_event)

    def progress_event(self, event):
        if event.get("event") == "progress":
            self.status.setText(f"{event['done'] + 1}/{event['total']}: {event['name']}")

    def finished_operation(self, process, result, code):
        if self.process is not process:
            return
        result = result or {"error": "Cloud worker stopped unexpectedly. Earlier files were retained."}
        self.process = None
        self._set_controls_enabled(True)
        if code or "error" in result:
            self.failures = min(self.failures + 1, 4)
            self.status.setText(result.get("error", "Cloud operation failed."))
        elif self.operation == "pair":
            self.failures = 0
            self.status.setText(f"Account paired. {len(result['documents'])} documents available.")
        else:
            self.failures = 0 if result["status"] == "ok" else min(self.failures + 1, 4)
            message = (f"{len(result['downloaded'])} new, {len(result['changed'])} updated, {len(result['unchanged'])} unchanged; "
                       f"{len(result['preserved'])} PDFs {'downloaded' if self.operation == 'server-sync' else 'generated'}, "
                       f"{result['exports_available']} available.")
            issues = result["failed"] + result["pdf_failed"]
            if issues:
                message += f" {len(issues)} issues: {issues[0]['error']}"
            if result["missing"]:
                message += f" {len(result['missing'])} absent from source; local copies retained."
            if result.get("server_status") and result["server_status"] != "ok":
                message += f" Server sync status: {result['server_status']}; available copies downloaded."
            if result.get("last_success"):
                message += f" Last full success: {result['last_success']}."
            self.status.setText(message)
            self.synced.emit(result["raw_library"])
        self._update_open_buttons()
        self.schedule()

    def show_library(self):
        path = str(self.destination / "raw")
        if self.parent() is not None:
            self.library_requested.emit(path)
        else:
            from .gui import MainWindow
            self.library_window = MainWindow(cloud_autostart=False)
            # The window adopts this controller rather than creating a second polling worker.
            self.library_window.adopt_sync_dialog(self)
            self.setParent(self.library_window, Qt.Dialog)
            self.library_window.open_folder(Path(path))
            self.library_window.show()
            self.show()

    def closeEvent(self, event):
        if self.process is not None:
            self.status.setText("Wait for the current cloud sync to finish before closing.")
            event.ignore()
        else:
            if self.parent() is None:
                self.timer.stop()
            super().closeEvent(event)


def run(destination: Path | None = None, *, automatic: bool = False, source=None, server=None):
    """Show the sync dialog on its own. ``destination`` None keeps the saved folder."""
    app = QApplication.instance() or QApplication(sys.argv)
    dialog = CloudSyncDialog(destination=destination, automatic=True if automatic else None, source=source, server=server)
    dialog.show()
    app.exec()
