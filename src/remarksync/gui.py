"""RemarkSync main window: library tree, page viewer, search, compilers.

The window is a caller of :class:`~remarksync.library.Library`; it holds
widgets and slots, not library state.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, __version__
from .backup import DocItem, open_rmdoc
from .library import IndexReport, Library, NoIndexError
from .mdcompile import markdown_to_rmdoc
from .layout import decode_frame, lay_out
from .rmdata import PageModel
from .viewer import PageCanvas, PdfUnderlay
from .workerprocess import start_json_worker

log = logging.getLogger(__name__)

# How long stopping a worker may hold the window. One that needs longer (a page
# of handwriting mid-recognition) finishes in the background instead.
STOP_WAIT_MS = 250


class DecodeWorker(QObject):
    """Decodes and lays out a document's pages on a worker thread, starting
    at the page the user asked for, then forward, then the rest. Each frame
    comes back as a signal; the window hands it to that page's canvas.

    Only plain page sizes are passed in. All PDF access stays on the GUI
    thread; separate MuPDF document handles do not make threading safe.
    """

    decoded = Signal(int, object)  # page index, PageFrame
    finished = Signal()

    def __init__(self, doc: DocItem, start: int = 0, *, base_sizes: dict[int, tuple[float, float]] | None = None):
        super().__init__()
        self._doc = doc
        self._start = max(0, min(start, max(0, len(doc.pages) - 1)))
        self._cancel = threading.Event()
        self._base_sizes = dict(base_sizes or {})

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        pages = self._doc.pages
        order = list(range(self._start, len(pages))) + list(range(self._start - 1, -1, -1))
        try:
            for index in order:
                if self._cancel.is_set():
                    break
                page = pages[index]
                size = self._base_sizes.get(index)
                try:
                    frame = decode_frame(page, size)
                except Exception:  # a bad page must not stop the document
                    log.exception("decoding page %d failed", index)
                    frame = lay_out(PageModel(error=f"Page {index + 1} could not be decoded"), base_size=size)
                self.decoded.emit(index, frame)
        finally:
            self.finished.emit()


class IndexWorker(QObject):
    """Runs a Library index refresh on a worker thread.

    The Library stays synchronous; this object is the only place that knows
    about threads. Progress comes back as signals, cancellation goes in as
    a flag checked between documents.
    """

    progressed = Signal(str, int)  # document name, documents done
    finished = Signal(object)  # IndexReport
    failed = Signal(str)

    def __init__(self, library: Library, force: bool):
        super().__init__()
        self._library = library
        self._force = force
        self._cancel = threading.Event()
        self._done = 0

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            report = self._library.refresh_index(progress=self, force=self._force)
        except Exception as exc:  # keep the UI alive whatever the indexer hit
            log.exception("indexing failed")
            self.failed.emit(str(exc))
            return
        self.finished.emit(report)

    def __call__(self, name: str) -> bool:
        """The index's progress callback: one document is done."""
        self._done += 1
        self.progressed.emit(name, self._done)
        return not self._cancel.is_set()

    def cancelled(self) -> bool:
        """Asked by the index between pages, so a long document stops too."""
        return self._cancel.is_set()


class MainWindow(QMainWindow):
    def __init__(self, *, cloud_autostart: bool = True):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1400, 900)

        self._library: Library | None = None
        self._index_thread: QThread | None = None
        self._index_worker: IndexWorker | None = None
        self._decode_thread: QThread | None = None
        self._decode_worker: DecodeWorker | None = None
        self._pending_frames: dict[int, object] = {}
        self._canvases: list[PageCanvas] = []
        self._underlay: PdfUnderlay | None = None
        self._export_process: QProcess | None = None
        self._scale = 0.62
        self._active_doc: DocItem | None = None
        self._doc_token = 0  # which document a decoded frame belongs to
        self._target_page: int | None = None
        self._page_errors: dict[int, str] = {}
        self._shown_hit = None
        self._handwriting_missing = False
        self._recognitions_before = 0
        self._cloud_dialog = None
        self._extraction = None  # the temporary folder behind an opened .rmdoc
        self._deferred = {}  # worker thread still stopping -> what to do once it has
        self._md_sender = None
        self._edit_dialogs: list = []
        self._fill_timer = QTimer(self)  # adds a long document's placeholder canvases in batches
        self._fill_timer.setInterval(50)
        self._fill_timer.timeout.connect(self._fill_batch)

        self.settings = QSettings("RemarkSync", APP_NAME)

        self._build_ui()
        self._restore_state()
        if cloud_autostart:
            from .cloudsync_dialog import sync_settings
            if sync_settings().value("automatic", False, type=bool):
                self.library_sync_action(show=False)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        def add_action(text, slot, shortcut=None, tip=None):
            act = toolbar.addAction(text)
            # QAction.triggered carries a checked bool argument; wrap the
            # slot so it never leaks into optional parameters (open_folder)
            act.triggered.connect(lambda _checked=False, s=slot: s())
            if shortcut:
                act.setShortcut(shortcut)
            if tip:
                act.setToolTip(tip)
            return act

        def add_toggle(text, key, tip, slot=None):
            """A checkable action whose state is remembered under ``key``."""
            act = toolbar.addAction(text)
            act.setCheckable(True)
            act.setToolTip(tip)
            act.setChecked(self.settings.value(key, False, type=bool))
            act.toggled.connect(lambda checked: self.settings.setValue(key, checked))
            if slot:
                act.toggled.connect(slot)
            return act

        add_action("Open Library Folder…", self.open_folder, "Ctrl+O", "Open a raw reMarkable backup directory")
        add_action("Open .rmdoc…", self.open_rmdoc_action, "Ctrl+R", "Open a single .rmdoc archive")
        add_action("Library sync…", self.library_sync_action, tip="Download updates from your central server or reMarkable Cloud")
        toolbar.addSeparator()
        add_action("Refresh", self.refresh, "F5")
        self.removed_action = add_toggle(
            "Show Removed", "show_removed",
            "Also list documents that were deleted on the tablet. Their preserved copies "
            "are always kept; this only chooses whether the tree shows them.", lambda _on: self._populate_tree())
        self.index_action = add_action(
            "Update Search Index", self.update_index, "Ctrl+I",
            "Index new and changed documents in the background (click again to cancel)",
        )
        add_action("Rebuild Search Index", self.rebuild_index, "Ctrl+Shift+I", "Re-index every document from scratch")
        self.handwriting_action = add_toggle(
            "Read Handwriting", "handwriting",
            "When indexing, also read handwriting (TrOCR on the GPU if installed, "
            "else the Windows ink recogniser); results are cached per page", self._on_handwriting_toggled)
        self._on_handwriting_toggled(self.handwriting_action.isChecked())
        add_action("Compile Markdown → .rmdoc…", self.compile_md_action, "Ctrl+M")
        add_action("Send Markdown to Tablet…", self.send_md_action, "Ctrl+Shift+M",
                   "Compile a Markdown file and send it to your reMarkable account as a new notebook")
        self.edit_action = add_action("Edit Document…", self.edit_doc_action, "Ctrl+D",
                                      "Edit the open notebook's typed text and pages, then save it or send it to the tablet")
        self.edit_action.setEnabled(False)
        self.export_action = add_action("Export Annotated PDF…", self.export_pdf_action, "Ctrl+E",
                                        "Save a preservation copy of the original PDF with visible highlights and notes")
        self.export_action.setEnabled(False)
        self.pdf_column_action = add_toggle("Match PDF Text Width", "pdf_column_notes_v2",
                                            "Wrap typed notes to the original PDF's body-text column when exporting")
        toolbar.addSeparator()
        add_action("Zoom −", lambda: self.zoom(-0.1), "Ctrl+-")
        add_action("Zoom +", lambda: self.zoom(+0.1), "Ctrl++")

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search library… (Enter)")
        self.search_box.setFixedWidth(280)
        self.search_box.returnPressed.connect(self.run_search)
        toolbar.addWidget(self.search_box)

        # left: document tree / center: pages
        splitter = QSplitter(Qt.Horizontal)
        self.doc_tree = QTreeWidget()
        self.doc_tree.setHeaderLabel("Documents")
        self.doc_tree.itemClicked.connect(self._on_tree_click)
        self.doc_tree.itemActivated.connect(self._on_tree_activated)  # Enter, for keyboard use
        splitter.addWidget(self.doc_tree)

        self.page_area = QScrollArea()
        self.page_area.setWidgetResizable(True)
        self.pages_container = QWidget()
        self._pages_layout = QVBoxLayout(self.pages_container)
        self._pages_layout.setSpacing(24)
        self._pages_layout.setContentsMargins(24, 24, 24, 24)
        self._pages_layout.addStretch(1)
        self.page_area.setWidget(self.pages_container)
        splitter.addWidget(self.page_area)
        splitter.setSizes([300, 1100])
        self.setCentralWidget(splitter)

        # right: search results dock
        self.results_list = QListWidget()
        self.results_list.itemClicked.connect(self._on_result_click)
        self.results_list.itemActivated.connect(self._on_result_activated)
        dock = QDockWidget("Search results", self)
        dock.setWidget(self.results_list)
        dock.setFeatures(QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.results_dock = dock

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Open a backup folder or a .rmdoc to begin.")

    def _restore_state(self) -> None:
        last = self.settings.value("library_path")
        if isinstance(last, (str, Path)) and Path(last).exists():
            self.open_folder(Path(last))

    # --------------------------------------------------------------- library

    def open_folder(self, path: Path | None = None) -> None:
        if not isinstance(path, (str, Path)):
            # None from code, or a stray bool from Qt signal wiring
            chosen = QFileDialog.getExistingDirectory(self, "Choose backup folder (a copy of the tablet's xochitl directory)")
            if not chosen:
                return
            path = Path(chosen)
        if not self._stop_indexing(then=lambda: self.open_folder(path)):  # a running index belongs to the old library
            return
        try:
            library = Library(path)
        except Exception as exc:
            self.statusBar().showMessage(f"Could not open library: {exc}")
            return
        self._library = library
        self.settings.setValue("library_path", str(path))
        self._populate_tree()
        gone = sum(1 for d in self._library.documents if d.uuid in self._library.absent)
        self.statusBar().showMessage(f"{path} — {len(self._library) - gone} documents" + (
            f"; {gone} removed from the tablet are kept" + ("" if self.removed_action.isChecked() else " (Show Removed lists them)")
            if gone else ""))

    def refresh(self) -> None:
        if self._library is not None:
            try:
                self._library.rescan()
            except Exception as exc:
                self.statusBar().showMessage(f"Could not refresh library: {exc}")
                return
            self._populate_tree()

    def _populate_tree(self) -> None:
        self.doc_tree.clear()
        if self._library is None:
            return

        absent = self._library.absent
        removed: list[tuple[DocItem, str]] = []  # with the folder path it sat in

        def add_items(parent_widget, items, path):
            for item in items:
                if item.kind == "doc":
                    if item.uuid in absent:
                        removed.append((item, path))
                        continue
                    label = f"🗎 {item.name}  ({len(item.pages)}p)"
                    node = QTreeWidgetItem(parent_widget, [label])
                else:
                    node = QTreeWidgetItem(parent_widget, [f"📁 {item.name}"])
                    add_items(node, item.children, f"{path}{item.name}/")
                    if item.uuid in absent and not node.childCount():
                        # a folder deleted on the tablet, with nothing left to show in it
                        (node.parent() or self.doc_tree.invisibleRootItem()).removeChild(node)
                        continue
                node.setData(0, Qt.UserRole, item)

        add_items(self.doc_tree, self._library.tree, "")
        self.doc_tree.expandToDepth(0)
        if removed and self.removed_action.isChecked():
            # after the expand, so the group starts collapsed
            group = QTreeWidgetItem(self.doc_tree, [f"🗑 Removed from tablet ({len(removed)})"])
            group.setToolTip(0, "Deleted on the tablet. The preserved copies stay here and can still be opened and searched.")
            for item, path in sorted(removed, key=lambda pair: pair[0].name.lower()):
                node = QTreeWidgetItem(group, [f"🗎 {item.name}  ({len(item.pages)}p)" + (f"  — was in {path[:-1]}" if path else "")])
                node.setForeground(0, self.palette().color(QPalette.Disabled, QPalette.Text))
                node.setToolTip(0, f"Gone from the tablet since {absent[item.uuid]}")
                node.setData(0, Qt.UserRole, item)

    def _on_tree_click(self, node: QTreeWidgetItem, _col: int) -> None:
        item = node.data(0, Qt.UserRole)
        if isinstance(item, DocItem):
            self.show_doc(item)

    def _on_tree_activated(self, node: QTreeWidgetItem, col: int) -> None:
        # a double-click activates too, after its click already opened the document
        if node.data(0, Qt.UserRole) is not self._active_doc:
            self._on_tree_click(node, col)

    # ---------------------------------------------------------------- pages

    def show_doc(self, doc: DocItem, page_no: int | None = None) -> None:
        """Open a document: placeholder canvases appear at once, a worker
        decodes the pages (the requested page first) and fills them in."""
        if not self._stop_decoding(then=lambda: self.show_doc(doc, page_no)):
            return
        self._fill_timer.stop()
        for canvas in self._canvases:
            self._pages_layout.removeWidget(canvas)
            canvas.deleteLater()
        self._canvases = []
        self._pending_frames = {}
        self._doc_token += 1

        if self._underlay:
            self._underlay.close()
            self._underlay = None
        base_pdf = doc.resolve_base_pdf()
        pdf_problem = ""
        if base_pdf:
            try:
                self._underlay = PdfUnderlay(base_pdf)
            except Exception as exc:
                # still open the document: the notes are readable without the page under them
                pdf_problem = f" — could not open base PDF, showing the notes without it: {exc}"

        self._active_doc = doc
        self.edit_action.setEnabled(doc.file_type == "notebook")
        self.export_action.setEnabled(self._export_process is None and self._can_export(doc))
        self._target_page = page_no
        self._page_errors = {}
        # placeholders are cheap; still add the long tail in batches so a
        # 1,000-page document does not lay out every widget in one go
        first = max(40, (page_no or 0) + 2)
        self._add_canvases(doc, min(first, len(doc.pages)))
        if len(doc.pages) > first:
            self._fill_timer.start()
        self._start_decoding(doc, page_no or 0)

        self.statusBar().showMessage(f"{doc.name} — {len(doc.pages)} pages [{doc.file_type}]{pdf_problem}")
        if page_no is not None and 0 <= page_no < len(self._canvases):
            self.page_area.ensureWidgetVisible(self._canvases[page_no], 0, 40)

    def _add_canvases(self, doc: DocItem, upto: int) -> None:
        while len(self._canvases) < min(upto, len(doc.pages)):
            index = len(self._canvases)
            page = doc.pages[index]
            frame = self._pending_frames.pop(index, None)
            canvas = PageCanvas(doc, page, self._underlay, self._scale, frame=frame)
            self._canvases.append(canvas)
            self._pages_layout.insertWidget(self._pages_layout.count() - 1, canvas, 0, Qt.AlignHCenter)

    def _fill_batch(self) -> None:
        doc = self._active_doc  # show_doc stops the timer before it changes this
        if doc is not None:
            self._add_canvases(doc, len(self._canvases) + 150)
        if doc is None or len(self._canvases) >= len(doc.pages):
            self._fill_timer.stop()

    @staticmethod
    def _can_export(doc: DocItem | None) -> bool:
        """Annotated-PDF export needs the original PDF to annotate."""
        return doc is not None and doc.file_type in ("pdf", "epub") and doc.resolve_base_pdf() is not None

    # -- background decoding ------------------------------------------------

    @property
    def is_decoding(self) -> bool:
        return self._decode_thread is not None and self._decode_thread.isRunning()

    def pages_pending(self) -> int:
        """Canvases still showing a placeholder."""
        return sum(1 for c in self._canvases if not c.decoded)

    def _start_decoding(self, doc: DocItem, start: int) -> None:
        sizes = {}
        if self._underlay is not None:
            for page in doc.pages:
                size = self._underlay.page_size(page.base_index)
                if size is not None:
                    sizes[page.index] = size
        worker = DecodeWorker(doc, start, base_sizes=sizes)
        thread = QThread(self)
        worker.moveToThread(thread)
        token = self._doc_token
        thread.started.connect(worker.run)
        worker.decoded.connect(lambda index, frame, t=token: self._on_page_decoded(t, index, frame))
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        self._decode_thread, self._decode_worker = thread, worker
        thread.start()

    def _defer_until_stopped(self, thread, then, what: str) -> None:
        """A worker that has not stopped within ``STOP_WAIT_MS`` finishes in the
        background rather than holding the window. ``then`` is what the caller
        was about to do; it runs when the thread ends (the latest request wins)."""
        if then is None:
            self.statusBar().showMessage(f"{what} is still stopping; try again when it finishes.")
            return
        self.statusBar().showMessage(f"{what} is still stopping; continuing when it finishes.")
        first = thread not in self._deferred
        self._deferred[thread] = then
        if first:
            thread.finished.connect(self._run_deferred)

    def _run_deferred(self) -> None:
        for thread in [t for t in self._deferred if t.isFinished()]:
            self._deferred.pop(thread)()

    def _stop_decoding(self, then=None) -> bool:
        if self._decode_thread is None:
            return True
        if self._decode_worker is not None:
            self._decode_worker.cancel()
        self._decode_thread.quit()
        if not self._decode_thread.wait(STOP_WAIT_MS):
            self._defer_until_stopped(self._decode_thread, then, "Page decoding")
            return False
        self._deferred.pop(self._decode_thread, None)
        self._decode_thread.deleteLater()
        self._decode_thread, self._decode_worker = None, None
        return True

    def _on_page_decoded(self, token: int, index: int, frame) -> None:
        if token != self._doc_token:
            return  # a frame from a document that has since been closed
        if frame.page.error:
            self._page_errors[index] = frame.page.error
            self.statusBar().showMessage(f"{len(self._page_errors)} unreadable page(s): {frame.page.error}")
        if index < len(self._canvases):
            self._canvases[index].set_frame(frame)
            if index == self._target_page:
                # sizes above the target may have changed; land on it again
                self.page_area.ensureWidgetVisible(self._canvases[index], 0, 40)
                self._target_page = None
        else:
            self._pending_frames[index] = frame

    def zoom(self, delta: float) -> None:
        self._scale = min(2.5, max(0.25, self._scale + delta))
        for canvas in self._canvases:
            canvas.set_scale(self._scale)

    # ---------------------------------------------------------------- search

    @property
    def is_indexing(self) -> bool:
        return self._index_thread is not None and self._index_thread.isRunning()

    def update_index(self) -> None:
        """Index new and changed documents; a second click cancels."""
        if self.is_indexing:
            self.cancel_indexing()
        else:
            self._start_indexing(force=False)

    def _on_handwriting_toggled(self, on: bool) -> None:
        self.handwriting_action.setText(f"Read Handwriting: {'On' if on else 'Off'}")

    def rebuild_index(self) -> None:
        """Re-index everything from scratch (cancels a running update first)."""
        if self._stop_indexing(then=self.rebuild_index):
            self._start_indexing(force=True)

    def cancel_indexing(self) -> None:
        if self._index_worker is not None:
            self._index_worker.cancel()
            self.statusBar().showMessage("Cancelling indexing…")

    def _start_indexing(self, force: bool) -> None:
        if self._library is None:
            self.statusBar().showMessage("Open a library folder first.")
            return
        # also lets go of the previous run's finished thread
        if not self._stop_indexing(then=lambda: self._start_indexing(force)):
            return
        self._library.handwriting = self.handwriting_action.isChecked()
        self._handwriting_missing = self.handwriting_action.isChecked() and not self._library.handwriting
        worker = IndexWorker(self._library, force)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progressed.connect(lambda name, done, w=worker: self._on_index_progress(name, done)
                                  if self._index_worker is w else None)
        worker.finished.connect(lambda report, w=worker: self._on_index_finished(report)
                                if self._index_worker is w else None)
        worker.failed.connect(lambda message, w=worker: self._on_index_failed(message)
                              if self._index_worker is w else None)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        self._index_thread, self._index_worker = thread, worker
        self.index_action.setText("Cancel Indexing")
        self._recognitions_before = len(self._library.recognition)
        self.statusBar().showMessage(f"Indexing{self._handwriting_note()}…")
        thread.start()

    def _handwriting_note(self) -> str:
        """Parenthetical for indexing status messages: the engine in use,
        or — when the toggle is on but none is installed — why ink is skipped."""
        engine = self._library.handwriting if self._library else None
        if engine:
            return f" (reading handwriting with {engine})"
        if self._handwriting_missing:
            return " (no handwriting engine installed — typed text only; see README: pip install -e .[handwriting])"
        return ""

    def _stop_indexing(self, then=None) -> bool:
        """Cancel a running index. False while it is still stopping; ``then``
        is run once it has."""
        if self._index_thread is None:
            return True
        self.cancel_indexing()
        self._index_thread.quit()
        if not self._index_thread.wait(STOP_WAIT_MS):
            self._defer_until_stopped(self._index_thread, then, "Indexing")
            return False
        self._deferred.pop(self._index_thread, None)
        self._index_thread.deleteLater()
        self._index_thread, self._index_worker = None, None
        self.index_action.setText("Update Search Index")
        return True

    def _on_index_progress(self, name: str, done: int) -> None:
        total = len(self._library) if self._library else 0
        self.statusBar().showMessage(f"Indexing {done}/{total}{self._handwriting_note()}: {name}")

    def _on_index_finished(self, report: IndexReport) -> None:
        self._index_worker = None  # it deletes itself; the thread is released by the next start or stop
        self.index_action.setText("Update Search Index")
        state = "Index updated" if report.completed else "Indexing cancelled"
        message = (f"{state}: {report.indexed} documents indexed ({report.pages} pages), "
                   f"{report.skipped} unchanged, {report.removed} removed, {report.failed} failed")
        if report.errors:
            message += f"; {report.errors[0]}"
        if self._library is not None and self._library.handwriting:
            read = len(self._library.recognition) - self._recognitions_before
            message += f"; handwriting: {read} pages read with {self._library.handwriting}, {len(self._library.recognition)} cached"
        else:
            message += self._handwriting_note()
        self.statusBar().showMessage(message)

    def _on_index_failed(self, message: str) -> None:
        self._index_worker = None
        self.index_action.setText("Update Search Index")
        self.statusBar().showMessage(f"Indexing failed: {message}")

    def run_search(self) -> None:
        query = self.search_box.text().strip()
        if not query or self._library is None:
            return
        try:
            hits = self._library.search(query)
        except NoIndexError:
            self.statusBar().showMessage("No search index — use “Update Search Index” first (Ctrl+I).")
            return
        self.results_list.clear()
        for hit in hits:
            widget_item = QListWidgetItem(f"{hit.doc_name} — p{hit.page_no + 1}\n{hit.snippet}")
            widget_item.setData(Qt.UserRole, hit)
            self.results_list.addItem(widget_item)
        self.statusBar().showMessage(f"{len(hits)} results for “{query}”")

    def _on_result_click(self, list_item: QListWidgetItem) -> None:
        hit = list_item.data(Qt.UserRole)
        doc = self._library.document(hit.doc_uuid) if self._library else None
        if doc is None:
            self.statusBar().showMessage("Document not in current library tree (opened archive?)")
            return
        self._shown_hit = hit
        self.show_doc(doc, page_no=hit.page_no)

    def _on_result_activated(self, list_item: QListWidgetItem) -> None:
        # a double-click activates too, after its click already opened the hit
        if list_item.data(Qt.UserRole) is not self._shown_hit:
            self._on_result_click(list_item)

    # -------------------------------------------------------------- rmdoc/md

    def open_rmdoc_action(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Open .rmdoc", "", "reMarkable documents (*.rmdoc *.rmn *.zip)")
        if not chosen:
            return
        # The archive is read from an extraction that lasts as long as it is shown.
        extraction = tempfile.TemporaryDirectory(prefix="remarksync_", ignore_cleanup_errors=True)
        try:
            doc = open_rmdoc(Path(chosen), Path(extraction.name))
        except Exception as exc:
            extraction.cleanup()
            self.statusBar().showMessage(f"Could not open {chosen}: {exc}")
            return
        self.show_doc(doc)
        if self._extraction is not None:
            self._extraction.cleanup()
        self._extraction = extraction

    def library_sync_action(self, *, show: bool = True) -> None:
        from .cloudsync_dialog import CloudSyncDialog
        if self._cloud_dialog is None:
            self.adopt_sync_dialog(CloudSyncDialog(self))
        if show:
            self._cloud_dialog.show()
            self._cloud_dialog.raise_()

    def adopt_sync_dialog(self, dialog) -> None:
        """Make ``dialog`` this window's one sync controller: its library
        requests open here and its finished syncs refresh the tree."""
        self._cloud_dialog = dialog
        dialog.library_requested.connect(lambda path: self.open_folder(Path(path)))
        dialog.synced.connect(self._cloud_synced)

    def _cloud_synced(self, path: str) -> None:
        if self._library is not None and Path(path).resolve() == self._library.root.resolve():
            self.refresh()

    def export_pdf_action(self) -> None:
        from .edit_dialog import safe_filename
        from .pdfexport import document_payload

        doc = self._active_doc
        if doc is None or self._export_process is not None:
            return
        chosen, _ = QFileDialog.getSaveFileName(self, "Save annotated PDF outside the raw backup",
                                               f"{safe_filename(doc.name)}-annotated.pdf", "PDF (*.pdf)")
        if not chosen:
            return
        self.export_action.setEnabled(False)
        self.statusBar().showMessage(f"Exporting {doc.name}…")
        self._export_process = start_json_worker(
            self, "remarksync.pdfexport_worker",
            {"document": document_payload(doc), "destination": chosen,
             "text_layout": "pdf-column" if self.pdf_column_action.isChecked() else "tablet", "overwrite": True},
            self._export_finished)

    def _export_finished(self, process, result, code) -> None:
        if self._export_process is not process:
            return
        if result is None:
            result = {"error": bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
                      or process.errorString()}
        if code or "error" in result:
            self.statusBar().showMessage(f"PDF export failed: {result.get('error', 'worker stopped')}")
        else:
            warnings = result.get("warnings", [])
            message = f"Exported {result['path']} — {result['pages']} pages"
            if warnings:
                message += f"; {len(warnings)} fidelity warnings in remarksync-manifest.json: {warnings[0]}"
            self.statusBar().showMessage(message)
        self._export_process = None
        self.export_action.setEnabled(self._can_export(self._active_doc))

    def compile_md_action(self) -> None:
        src, _ = QFileDialog.getOpenFileName(self, "Choose Markdown file", "", "Markdown (*.md *.markdown *.txt)")
        if not src:
            return
        default_out = str(Path(src).with_suffix(".rmdoc"))
        out, _ = QFileDialog.getSaveFileName(self, "Save .rmdoc as", default_out, "reMarkable document (*.rmdoc)")
        if not out:
            return
        # compile for the device this library came off, so a Paper Pro gets its
        # taller page instead of the smaller one every reMarkable understands
        try:
            paper = self._paper_size()
            path = markdown_to_rmdoc(Path(src).read_text(encoding="utf-8"), Path(src).stem, Path(out), paper)
        except Exception as exc:
            self.statusBar().showMessage(f"Compile failed: {exc}")
            return
        for_paper = f" for {self._paper_words(paper)}" if paper else ""
        self.statusBar().showMessage(f"Compiled {path}{for_paper} — upload it via the tablet's USB web UI.")

    def _paper_size(self) -> tuple[float, float] | None:
        """The page size Markdown is compiled for: this library's tablet, if it says."""
        return self._library.paper_size if self._library is not None else None

    @staticmethod
    def _paper_words(paper: tuple[float, float]) -> str:
        return f"{paper[0]:.0f}x{paper[1]:.0f} pages"

    def send_md_action(self) -> None:
        from .edit_dialog import Sender, describe, keep_archive

        sender = self._md_sender
        if sender is None:
            self._md_sender = sender = Sender(self)
            sender.finished.connect(lambda result: self.statusBar().showMessage(describe(result)))
        if sender.busy:
            self.statusBar().showMessage("A document is still being sent.")
            return
        src, _ = QFileDialog.getOpenFileName(self, "Choose Markdown file to send", "", "Markdown (*.md *.markdown *.txt)")
        if not src:
            return
        name, ok = QInputDialog.getText(self, "Send Markdown to tablet", "Name on the tablet:", text=Path(src).stem)
        name = name.strip()
        if not ok or not name:
            return
        try:
            from .mdcompile import build_rmdoc, markdown_to_paragraphs, paginate
            paper = self._paper_size()
            pages = paginate(markdown_to_paragraphs(Path(src).read_text(encoding="utf-8")), paper)
            data = build_rmdoc(name, pages, paper)
        except Exception as exc:
            self.statusBar().showMessage(f"Compile failed: {exc}")
            return
        for_paper = f", laid out for {self._paper_words(paper)}" if paper else ""
        if QMessageBox.question(self, "Send to tablet", f"Send '{name}' ({len(pages)} pages{for_paper}) to your "
                                "reMarkable account as a new notebook?") != QMessageBox.Yes:
            return
        self.statusBar().showMessage(f"Sending '{name}' to the cloud…")
        sender.send(keep_archive(data), name)

    def edit_doc_action(self) -> None:
        from .edit_dialog import EditDialog
        from .rmedit import Draft, EditError

        doc = self._active_doc
        if doc is None:
            return
        try:
            draft = Draft.from_document(doc)
        except (EditError, OSError) as exc:
            self.statusBar().showMessage(f"Cannot edit {doc.name}: {exc}")
            return
        dialog = EditDialog(draft, self)
        # a closed dialog is finished with, unless it is still sending
        for closed in [d for d in self._edit_dialogs if not d.isVisible() and not d.sender_.busy]:
            self._edit_dialogs.remove(closed)
            closed.deleteLater()
        self._edit_dialogs.append(dialog)
        dialog.show()

    def closeEvent(self, event) -> None:  # noqa: N802
        # a hidden edit dialog can still be sending: closing now would cut a replace in half
        if (self._md_sender is not None and self._md_sender.busy) or any(d.sender_.busy for d in self._edit_dialogs):
            self.statusBar().showMessage("A document is still being sent; wait for it to finish before closing.")
            event.ignore()
            return
        if self._cloud_dialog is not None and self._cloud_dialog.process is not None:
            self.statusBar().showMessage("Wait for the library sync to finish before closing.")
            event.ignore()
            return
        if self._export_process is not None:
            self.statusBar().showMessage("PDF export is still running; wait for it to finish before closing.")
            event.ignore()
            return
        if not self._stop_indexing(then=self.close) or not self._stop_decoding(then=self.close):
            event.ignore()  # the window closes itself once the worker has stopped
            return
        super().closeEvent(event)


def run() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
