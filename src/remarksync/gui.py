"""RemarkSync main window: library tree, page viewer, search, compilers.

The window is a caller of :class:`~remarksync.library.Library`; it holds
widgets and slots, not library state.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressDialog,
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
from .library import Library, NoIndexError
from .mdcompile import markdown_to_rmdoc
from .viewer import PageCanvas, PdfUnderlay


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1400, 900)

        self._library: Library | None = None
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
        add_action("Rebuild Search Index", self.rebuild_index, "Ctrl+I")
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
        if hasattr(self, "_fill_timer"):
            self._fill_timer.stop()
        for canvas in self._canvases:
            self._pages_layout.removeWidget(canvas)
            canvas.deleteLater()
        self._canvases = []
        self._doc_token = getattr(self, "_doc_token", 0) + 1

        if self._underlay:
            self._underlay.close()
            self._underlay = None
        base_pdf = doc.resolve_base_pdf()
        if base_pdf:
            self._underlay = PdfUnderlay(base_pdf)

        self._active_doc = doc
        # render the first screen synchronously; large docs (1000+ page
        # PDFs) stream the rest in batches so the UI never blocks
        first = max(40, (page_no or 0) + 2)
        self._add_canvases(doc, min(first, len(doc.pages)))
        if len(doc.pages) > first:
            self._fill_timer = QTimer(self)
            self._fill_timer.timeout.connect(lambda: self._fill_batch(doc, self._doc_token))
            self._fill_timer.start(50)

        self.statusBar().showMessage(f"{doc.name} — {len(doc.pages)} pages [{doc.file_type}]")
        if page_no is not None and 0 <= page_no < len(self._canvases):
            target = self._canvases[page_no]
            self.page_area.ensureWidgetVisible(target, 0, 40)

    def _add_canvases(self, doc: DocItem, upto: int) -> None:
        while len(self._canvases) < min(upto, len(doc.pages)):
            page = doc.pages[len(self._canvases)]
            canvas = PageCanvas(doc, page, self._underlay, self._scale)
            self._canvases.append(canvas)
            self._pages_layout.insertWidget(self._pages_layout.count() - 1, canvas, 0, Qt.AlignHCenter)

    def _fill_batch(self, doc: DocItem, token: int) -> None:
        if token != self._doc_token or doc is not getattr(self, "_active_doc", None):
            return
        self._add_canvases(doc, len(self._canvases) + 150)
        if len(self._canvases) >= len(doc.pages):
            self._fill_timer.stop()

    def zoom(self, delta: float) -> None:
        self._scale = min(2.5, max(0.25, self._scale + delta))
        for canvas in self._canvases:
            canvas.set_scale(self._scale)

    # ---------------------------------------------------------------- search

    def rebuild_index(self) -> None:
        if self._library is None:
            self.statusBar().showMessage("Open a library folder first.")
            return
        progress = QProgressDialog("Building search index…", "Cancel", 0, max(1, len(self._library)), self)
        progress.setWindowModality(Qt.WindowModal)

        def step(name: str) -> bool:
            progress.setValue(progress.value() + 1)
            progress.setLabelText(f"Indexing {name}")
            QApplication.processEvents()
            return not progress.wasCanceled()

        report = self._library.rebuild_index(progress=step)
        if report.completed:
            self.statusBar().showMessage(f"Index built: {report.pages} pages")
        else:
            self.statusBar().showMessage(f"Index build cancelled after {report.pages} pages")

    def run_search(self) -> None:
        query = self.search_box.text().strip()
        if not query or self._library is None:
            return
        try:
            hits = self._library.search(query)
        except NoIndexError:
            self.statusBar().showMessage("No search index — use “Rebuild Search Index” first (Ctrl+I).")
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


def run() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
