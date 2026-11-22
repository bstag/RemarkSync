"""Edit a notebook's pages and typed text, then save it or send it to the tablet.

The dialog holds a :class:`~remarksync.rmedit.Draft`; the document it was
opened from is never written. Sending runs in the cloud worker process, like
every other cloud operation, and only after the user confirms it.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Qt, Signal
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QMessageBox,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from .rmedit import Draft, EditError
from .workerprocess import start_json_worker

SENT = Path(__file__).resolve().parents[2] / "output" / "sent"
HELP = ("One line per paragraph.  # Heading   ## Subheading   ### Bold   - bullet   1. numbered   "
        "- [ ] checkbox   - [x] ticked   (two spaces before a marker nests it)")


def safe_filename(name: str) -> str:
    """A document name as a file name Windows accepts."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "Document"


def keep_archive(data: bytes, root: Path = SENT) -> Path:
    """Store what is about to be sent, named by its content, so a retry sends
    the same bytes and the sent archive can be found afterwards."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / (hashlib.sha256(data).hexdigest()[:16] + ".rmdoc")
    if not path.is_file():
        path.write_bytes(data)
    return path


class Sender(QObject):
    """One send through the cloud worker process. Emits the worker's result."""

    finished = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process: QProcess | None = None

    @property
    def busy(self) -> bool:
        return self.process is not None

    def send(self, archive: Path, name: str, *, replace: dict[str, str] | None = None, root: Path = SENT) -> None:
        if self.busy:
            return
        self.process = start_json_worker(
            self, "remarksync.cloud_worker",
            {"operation": "send", "archive": str(archive), "name": name, "replace": replace, "keep": str(root / "replaced")},
            self._done)

    def _done(self, process: QProcess, result: dict | None, _code: int) -> None:
        if self.process is not process:
            return
        self.process = None
        self.finished.emit(result or {
            "error": "The cloud worker stopped unexpectedly. Send again: an upload that did arrive is recognised."})


def describe(result: dict) -> str:
    """A send result in the words the status line uses."""
    if "error" in result:
        return f"Not sent: {result['error']}"
    if result.get("status") == "already-sent":
        return f"'{result['name']}' is already in the cloud with exactly this content; nothing was sent."
    if result.get("replaced"):
        return (f"Replaced '{result['name']}' in the cloud. The tablet shows it after its next sync. "
                f"The previous revision is kept at {result['kept']}.")
    return f"Sent '{result['name']}' to the cloud. It appears on the tablet after its next sync."


class EditDialog(QDialog):
    def __init__(self, draft: Draft, parent=None, *, sent_root: Path = SENT):
        super().__init__(parent)
        self.draft, self.sent_root = draft, sent_root
        self.setWindowTitle(f"Edit — {draft.name}")
        self.resize(1000, 700)
        self.sender_ = Sender(self)
        self.sender_.finished.connect(self._sent)
        self._replaced = False
        self._replacing = False
        self._unsaved = False  # edits made since the draft was last saved or sent
        self._loading = False

        outer = QVBoxLayout(self)
        body = QHBoxLayout()
        outer.addLayout(body, 1)

        left = QVBoxLayout()
        self.page_list = QListWidget()
        self.page_list.setAccessibleName("Pages")
        self.page_list.currentRowChanged.connect(self._show_page)
        left.addWidget(self.page_list, 1)
        self.page_buttons = []
        for label, slot in (("Move up", lambda: self._move(-1)), ("Move down", lambda: self._move(1)),
                            ("Duplicate", self._duplicate), ("Add text page", self._add), ("Delete page", self._delete)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            left.addWidget(button)
            self.page_buttons.append(button)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.PlainText)
        right.addWidget(self.note)
        self.editor = QPlainTextEdit()
        self.editor.setAccessibleName("Typed text of the selected page")
        self.editor.textChanged.connect(self._text_changed)
        right.addWidget(self.editor, 1)
        body.addLayout(right, 3)

        actions = QHBoxLayout()
        self.save_button = QPushButton("Save as .rmdoc…")
        self.save_button.clicked.connect(self.save_as)
        self.copy_button = QPushButton("Send to tablet as a new copy…")
        self.copy_button.clicked.connect(self.send_copy)
        self.replace_button = QPushButton("Replace on tablet…")
        self.replace_button.clicked.connect(self.send_replace)
        for button in (self.save_button, self.copy_button, self.replace_button):
            actions.addWidget(button)
        outer.addLayout(actions)
        self.status = QLabel("Nothing is changed on the tablet or in your library until you save or send.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        outer.addWidget(self.status)

        self._refill(0)

    # ------------------------------------------------------------------ pages

    def _refill(self, select: int) -> None:
        self._loading = True
        self.page_list.clear()
        for number, page in enumerate(self.draft.pages, 1):
            text = (page.text or "").strip().splitlines()
            first = text[0][:40] if text else ("(ink only)" if page.locked else "(blank)")
            self.page_list.addItem(f"{number}. {first}" + (" *" if page.changed or page.source != page.page_id else ""))
        self._loading = False
        self.page_list.setCurrentRow(max(0, min(select, len(self.draft.pages) - 1)))
        self._update_buttons()

    def _show_page(self, row: int) -> None:
        if self._loading or row < 0:
            return
        page = self.draft.pages[row]
        self._loading = True
        self.editor.setPlainText(page.text or "")
        self.editor.setReadOnly(bool(page.locked))
        self._loading = False
        self.note.setText(f"This page's text cannot be edited: {page.locked}. It can still be moved, copied or deleted."
                          if page.locked else HELP)

    def _text_changed(self) -> None:
        row = self.page_list.currentRow()
        if self._loading or row < 0 or self.draft.pages[row].locked:
            return
        self.draft.set_text(row, self.editor.toPlainText())
        self._edited()
        self._update_buttons()

    def _edited(self) -> None:
        self._unsaved = self.draft.changed

    def _update_buttons(self) -> None:
        busy = self.sender_.busy
        for button in self.page_buttons + [self.save_button]:
            button.setEnabled(not busy)
        self.copy_button.setEnabled(not busy)
        self.replace_button.setEnabled(not busy and self.draft.changed and not self._replaced)
        self.replace_button.setToolTip(
            "Already replaced. Sync, then open the document again to keep editing." if self._replaced else
            "Puts this edited version in place of the document on your tablet. Refused if the tablet has changed it "
            "since this copy was synced.")

    def _move(self, by: int) -> None:
        row, to = self.page_list.currentRow(), self.page_list.currentRow() + by
        if 0 <= row and 0 <= to < len(self.draft.pages):
            self.draft.move(row, to)
            self._edited()
            self._refill(to)

    def _duplicate(self) -> None:
        row = self.page_list.currentRow()
        if row >= 0:
            self.draft.duplicate(row)
            self._edited()
            self._refill(row + 1)

    def _add(self) -> None:
        row = self.page_list.currentRow()
        self.draft.add_page(row)
        self._edited()
        self._refill(row + 1)

    def _delete(self) -> None:
        row = self.page_list.currentRow()
        try:
            self.draft.delete(row)
        except (EditError, IndexError) as exc:
            self.status.setText(str(exc))
            return
        self._edited()
        self._refill(row)

    # ------------------------------------------------------------- save / send

    def _build(self, **kwargs) -> bytes | None:
        try:
            return self.draft.build(**kwargs)
        except Exception as exc:  # an edit that cannot be written must not take the dialog down
            self.status.setText(f"Could not build the edited document: {exc}")
            return None

    def save_as(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(self, "Save edited copy as",
                                                f"{safe_filename(self.draft.name)} (edited).rmdoc",
                                                "reMarkable document (*.rmdoc)")
        if not chosen:
            return
        data = self._build(new_identity=True, name=Path(chosen).stem)
        if data is not None:
            try:
                Path(chosen).write_bytes(data)
            except OSError as exc:
                self.status.setText(f"Could not save {chosen}: {exc}")
                return
            self._unsaved = False
            self.status.setText(f"Saved {chosen}. It imports as a new document, separate from the original.")

    def send_copy(self) -> None:
        name, ok = QInputDialog.getText(self, "Send as a new copy", "Name on the tablet:", text=f"{self.draft.name} (edited)")
        name = name.strip()
        if not ok or not name:
            return
        if QMessageBox.question(self, "Send to tablet", f"Send '{name}' to your reMarkable account as a new document?\n\n"
                                "The original stays as it is.") != QMessageBox.Yes:
            return
        data = self._build(new_identity=True, name=name)
        if data is not None:
            self._start(keep_archive(data, self.sent_root), name, None)

    def send_replace(self) -> None:
        answer = QMessageBox.warning(
            self, "Replace on tablet",
            f"Replace '{self.draft.name}' on your tablet with this edited version?\n\n"
            "• If the tablet has changed the document since this copy was synced, nothing is sent.\n"
            "• The version now in the cloud is saved on this PC first.\n"
            "• Edits made on the tablet that it has not synced yet can be lost. Let the tablet sync first.\n"
            "• The cloud removes the old version before the new one is uploaded. If the upload then fails, "
            "the document is missing from the tablet until you send the saved version again.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return
        data = self._build()
        if data is not None:
            self._start(keep_archive(data, self.sent_root), self.draft.name, self.draft.base_files)

    def _start(self, archive: Path, name: str, replace: dict[str, str] | None) -> None:
        self._replacing = replace is not None
        self.status.setText("Replacing in the cloud…" if self._replacing else "Sending to the cloud…")
        self.sender_.send(archive, name, replace=replace, root=self.sent_root)
        self._update_buttons()

    def _sent(self, result: dict) -> None:
        if "error" not in result:
            self._unsaved = False
            if self._replacing:
                self._replaced = True
        self.status.setText(describe(result))
        self._update_buttons()

    # ---------------------------------------------------------------- closing

    def _may_close(self) -> bool:
        """Closing abandons a running send and throws the draft away, so a
        send must finish first and unsaved edits are only dropped on a yes."""
        if self.sender_.busy:
            self.status.setText("Wait for the send to finish before closing.")
            return False
        if self._unsaved and QMessageBox.question(
                self, "Discard edits", f"Close without saving or sending your edits to '{self.draft.name}'?\n\n"
                "They are not kept anywhere else.") != QMessageBox.Yes:
            return False
        return True

    def reject(self) -> None:
        # Esc arrives here without a close event, and so does the close
        # button: QDialog turns a visible dialog's close into reject().
        if self._may_close():
            super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self.isVisible() and self.sender_.busy:  # a hidden dialog is not asked through reject()
            event.ignore()
        else:
            super().closeEvent(event)
