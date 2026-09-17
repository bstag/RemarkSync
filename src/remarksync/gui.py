"""RemarkSync main window: library tree, page viewer, search, compilers.

The window is a caller of :class:`~remarksync.library.Library`; it holds
widgets and slots, not library state.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
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
from .viewer import PageCanvas, PdfUnderlay, decode_frame

log = logging.getLogger(__name__)


class DecodeWorker(QObject):
    """Decodes and lays out a document's pages on a worker thread, starting
    at the page the user asked for, then forward, then the rest. Each frame
    comes back as a signal; the window hands it to that page's canvas.

    Decoding is pure Python (rmscene) and needs no Qt; the only shared
    resource is the base PDF, which PyMuPDF must not use from two threads,
    so the worker opens its own handle just to read page sizes.
    """

    decoded = Signal(int, object)  # page index, PageFrame
    finished = Signal()

    def __init__(self, doc: DocItem, start: int = 0):
        super().__init__()
        self._doc = doc
        self._start = max(0, min(start, max(0, len(doc.pages) - 1)))
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        pages = self._doc.pages
        order = list(range(self._start, len(pages))) + list(range(self._start - 1, -1, -1))
        base = None
        base_pdf = self._doc.resolve_base_pdf()
        try:
            if base_pdf:
                import fitz

                base = fitz.open(base_pdf)
            for index in order:
                if self._cancel.is_set():
                    break
                page = pages[index]
                size = None
                if base is not None and index < base.page_count:
                    rect = base.load_page(index).rect
                    size = (float(rect.width), float(rect.height))
                try:
                    frame = decode_frame(page, size)
                except Exception:  # a bad page must not stop the document
                    log.exception("decoding page %d failed", index)
                    continue
                self.decoded.emit(index, frame)
        finally:
            if base is not None:
                base.close()
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
            refresh = self._library.rebuild_index if self._force else self._library.refresh_index
            report = refresh(progress=self._step)
        except Exception as exc:  # keep the UI alive whatever the indexer hit
            log.exception("indexing failed")
            self.failed.emit(str(exc))
            return
        self.finished.emit(report)

    def _step(self, name: str) -> bool:
        self._done += 1
        self.progressed.emit(name, self._done)
        return not self._cancel.is_set()


class MainWindow(QMainWindow):
    def __init__(self):
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
        self._scale = 0.62

        self.settings = QSettings("RemarkSync", APP_NAME)

        self._build_ui()
        self._restore_state()

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

        add_action("Open Library Folder…", self.open_folder, "Ctrl+O", "Open a raw reMarkable backup directory")
        add_action("Open .rmdoc…", self.open_rmdoc_action, "Ctrl+R", "Open a single .rmdoc archive")
        toolbar.addSeparator()
        add_action("Refresh", self.refresh, "F5")
        self.index_action = add_action(
            "Update Search Index", self.update_index, "Ctrl+I",
            "Index new and changed documents in the background (click again to cancel)",
        )
        add_action("Rebuild Search Index", self.rebuild_index, "Ctrl+Shift+I", "Re-index every document from scratch")
        self.handwriting_action = toolbar.addAction("Read Handwriting")
        self.handwriting_action.setCheckable(True)
        self.handwriting_action.setToolTip("When indexing, also read handwriting (TrOCR on the GPU if installed, "
                                           "else the Windows ink recogniser); results are cached per page")
        self.handwriting_action.toggled.connect(self._on_handwriting_toggled)
        self.handwriting_action.setChecked(self.settings.value("handwriting", False, type=bool))
        self._on_handwriting_toggled(self.handwriting_action.isChecked())
        add_action("Compile Markdown → .rmdoc…", self.compile_md_action, "Ctrl+M")
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
            chosen = QFileDialog.getExistingDirectory(self, "Choose backup folder (xochitl mirror)")
            if not chosen:
                return
            path = Path(chosen)
        self._stop_indexing()  # a running index belongs to the old library
        self._library = Library(path)
        self.settings.setValue("library_path", str(path))
        self._populate_tree()
        self.statusBar().showMessage(f"{path} — {len(self._library)} documents")

    def refresh(self) -> None:
        if self._library is not None:
            self._library.refresh()
            self._populate_tree()

    def _populate_tree(self) -> None:
        self.doc_tree.clear()
        if self._library is None:
            return

        def add_items(parent_widget, items):
            for item in items:
                if item.kind == "doc":
                    label = f"🗎 {item.name}  ({len(item.pages)}p)"
                    node = QTreeWidgetItem(parent_widget, [label])
                else:
                    node = QTreeWidgetItem(parent_widget, [f"📁 {item.name}"])
                    add_items(node, item.children)
                node.setData(0, Qt.UserRole, item)

        add_items(self.doc_tree, self._library.tree)
        self.doc_tree.expandToDepth(0)

    def _on_tree_click(self, node: QTreeWidgetItem, _col: int) -> None:
        item = node.data(0, Qt.UserRole)
        if isinstance(item, DocItem):
            self.show_doc(item)

    # ---------------------------------------------------------------- pages

    def show_doc(self, doc: DocItem, page_no: int | None = None) -> None:
        """Open a document: placeholder canvases appear at once, a worker
        decodes the pages (the requested page first) and fills them in."""
        self._stop_decoding()
        if hasattr(self, "_fill_timer"):
            self._fill_timer.stop()
        for canvas in self._canvases:
            self._pages_layout.removeWidget(canvas)
            canvas.deleteLater()
        self._canvases = []
        self._pending_frames = {}
        self._doc_token = getattr(self, "_doc_token", 0) + 1

        if self._underlay:
            self._underlay.close()
            self._underlay = None
        base_pdf = doc.resolve_base_pdf()
        if base_pdf:
            self._underlay = PdfUnderlay(base_pdf)

        self._active_doc = doc
        self._target_page = page_no
        # placeholders are cheap; still add the long tail in batches so a
        # 1,000-page document does not lay out every widget in one go
        first = max(40, (page_no or 0) + 2)
        self._add_canvases(doc, min(first, len(doc.pages)))
        if len(doc.pages) > first:
            self._fill_timer = QTimer(self)
            self._fill_timer.timeout.connect(lambda: self._fill_batch(doc, self._doc_token))
            self._fill_timer.start(50)
        self._start_decoding(doc, page_no or 0)

        self.statusBar().showMessage(f"{doc.name} — {len(doc.pages)} pages [{doc.file_type}]")
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

    def _fill_batch(self, doc: DocItem, token: int) -> None:
        if token != self._doc_token or doc is not getattr(self, "_active_doc", None):
            return
        self._add_canvases(doc, len(self._canvases) + 150)
        if len(self._canvases) >= len(doc.pages):
            self._fill_timer.stop()

    # -- background decoding ------------------------------------------------

    @property
    def is_decoding(self) -> bool:
        return self._decode_thread is not None and self._decode_thread.isRunning()

    def pages_pending(self) -> int:
        """Canvases still showing a placeholder."""
        return sum(1 for c in self._canvases if not c.decoded)

    def _start_decoding(self, doc: DocItem, start: int) -> None:
        worker = DecodeWorker(doc, start)
        thread = QThread(self)
        worker.moveToThread(thread)
        token = self._doc_token
        thread.started.connect(worker.run)
        worker.decoded.connect(lambda index, frame, t=token: self._on_page_decoded(t, index, frame))
        worker.finished.connect(thread.quit)
        self._decode_thread, self._decode_worker = thread, worker
        thread.start()

    def _stop_decoding(self) -> None:
        if self._decode_thread is None:
            return
        if self._decode_worker is not None:
            self._decode_worker.cancel()
        self._decode_thread.quit()
        self._decode_thread.wait(30_000)
        self._decode_thread, self._decode_worker = None, None

    def _on_page_decoded(self, token: int, index: int, frame) -> None:
        if token != self._doc_token:
            return  # a frame from a document that has since been closed
        if index < len(self._canvases):
            self._canvases[index].set_frame(frame)
            if index == getattr(self, "_target_page", None):
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
        self.settings.setValue("handwriting", on)
        self.handwriting_action.setText(f"Read Handwriting: {'On' if on else 'Off'}")

    def rebuild_index(self) -> None:
        """Re-index everything from scratch (cancels a running update first)."""
        self._stop_indexing()
        self._start_indexing(force=True)

    def cancel_indexing(self) -> None:
        if self._index_worker is not None:
            self._index_worker.cancel()
            self.statusBar().showMessage("Cancelling indexing…")

    def _start_indexing(self, force: bool) -> None:
        if self._library is None:
            self.statusBar().showMessage("Open a library folder first.")
            return
        self._library.handwriting = self.handwriting_action.isChecked()
        self._handwriting_missing = self.handwriting_action.isChecked() and not self._library.handwriting
        worker = IndexWorker(self._library, force)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progressed.connect(self._on_index_progress)
        worker.finished.connect(self._on_index_finished)
        worker.failed.connect(self._on_index_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
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
        if getattr(self, "_handwriting_missing", False):
            return " (no handwriting engine installed — typed text only; see README: pip install -e .[handwriting])"
        return ""

    def _stop_indexing(self) -> None:
        """Cancel a running index and wait for its thread to finish."""
        if self._index_thread is None:
            return
        self.cancel_indexing()
        self._index_thread.quit()
        self._index_thread.wait(30_000)
        self._index_thread, self._index_worker = None, None
        self.index_action.setText("Update Search Index")

    def _on_index_progress(self, name: str, done: int) -> None:
        total = len(self._library) if self._library else 0
        self.statusBar().showMessage(f"Indexing {done}/{total}{self._handwriting_note()}: {name}")

    def _on_index_finished(self, report: IndexReport) -> None:
        self.index_action.setText("Update Search Index")
        state = "Index updated" if report.completed else "Indexing cancelled"
        message = (f"{state}: {report.indexed} documents indexed ({report.pages} pages), "
                   f"{report.skipped} unchanged, {report.removed} removed")
        if self._library is not None and self._library.handwriting:
            read = len(self._library.recognition) - self._recognitions_before
            message += f"; handwriting: {read} pages read with {self._library.handwriting}, {len(self._library.recognition)} cached"
        else:
            message += self._handwriting_note()
        self.statusBar().showMessage(message)

    def _on_index_failed(self, message: str) -> None:
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
        self.results_dock.show()
        self.statusBar().showMessage(f"{len(hits)} results for “{query}”")

    def _on_result_click(self, list_item: QListWidgetItem) -> None:
        hit = list_item.data(Qt.UserRole)
        doc = self._library.document(hit.doc_uuid) if self._library else None
        if doc is None:
            self.statusBar().showMessage("Document not in current library tree (opened archive?)")
            return
        self.show_doc(doc, page_no=hit.page_no)

    # -------------------------------------------------------------- rmdoc/md

    def open_rmdoc_action(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Open .rmdoc", "", "reMarkable documents (*.rmdoc *.rmn *.zip)")
        if not chosen:
            return
        try:
            doc = open_rmdoc(Path(chosen))
        except Exception as exc:
            self.statusBar().showMessage(f"Could not open {chosen}: {exc}")
            return
        self.show_doc(doc)

    def compile_md_action(self) -> None:
        src, _ = QFileDialog.getOpenFileName(self, "Choose Markdown file", "", "Markdown (*.md *.markdown *.txt)")
        if not src:
            return
        default_out = str(Path(src).with_suffix(".rmdoc"))
        out, _ = QFileDialog.getSaveFileName(self, "Save .rmdoc as", default_out, "reMarkable document (*.rmdoc)")
        if not out:
            return
        try:
            path = markdown_to_rmdoc(Path(src).read_text(encoding="utf-8"), Path(src).stem, Path(out))
        except Exception as exc:
            self.statusBar().showMessage(f"Compile failed: {exc}")
            return
        self.statusBar().showMessage(f"Compiled {path} — upload it via the tablet's USB web UI.")


    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_indexing()
        self._stop_decoding()
        super().closeEvent(event)


def run() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
