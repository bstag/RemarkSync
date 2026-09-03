"""Offscreen GUI smoke test: window builds, library opens, pages render."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_opens_library(qapp, backup_root, tmp_path, monkeypatch):
    from remarksync.gui import MainWindow

    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    win = MainWindow()
    win.open_folder(backup_root)
    assert win.doc_tree.topLevelItemCount() == 2

    # find and open the ink notebook from the tree
    def find_doc(node):
        item = node.data(0, 0x0100)  # Qt.UserRole
        from remarksync.library import DocItem

        if isinstance(item, DocItem):
            return item
        for i in range(node.childCount()):
            found = find_doc(node.child(i))
            if found:
                return found
        return None

    doc = find_doc(win.doc_tree.topLevelItem(0))
    assert doc is not None
    win.show_doc(doc)
    assert len(win._canvases) == len(doc.pages)

    # force a render pass of the first page to catch painting errors
    from PySide6.QtGui import QPixmap

    pixmap = win._canvases[0].grab()
    assert not pixmap.isNull()
    win.close()


def test_open_folder_tolerates_signal_bool(qapp, backup_root, monkeypatch):
    """Regression: QAction.triggered passes checked=False into the slot,
    which must still open the folder dialog instead of crashing."""
    from PySide6.QtWidgets import QFileDialog

    from remarksync.gui import MainWindow

    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *a, **k: str(backup_root)
    )
    win = MainWindow()
    win.open_folder(False)  # exactly what the toolbar click produced
    assert win.doc_tree.topLevelItemCount() == 2
    win.close()
