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
        from remarksync.backup import DocItem

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


def test_background_indexing_then_search(qapp, backup_root, monkeypatch):
    """Indexing runs on a worker thread; the window stays responsive and the
    finished signal lands back on the UI thread."""
    import time

    from remarksync.gui import MainWindow

    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    win = MainWindow()
    win.open_folder(backup_root)
    assert not win.is_indexing

    win.update_index()
    assert win.is_indexing
    assert win.index_action.text() == "Cancel Indexing"
    deadline = time.monotonic() + 20
    while win.is_indexing and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert not win.is_indexing
    assert win.index_action.text() == "Update Search Index"
    assert "Index updated" in win.statusBar().currentMessage()

    win.search_box.setText("flux capacitor")
    win.run_search()
    assert win.results_list.count() >= 1
    win.close()


def test_opening_another_folder_stops_indexing(qapp, backup_root, tmp_path, monkeypatch):
    from remarksync.gui import MainWindow

    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    win = MainWindow()
    win.open_folder(backup_root)
    win.rebuild_index()
    other = tmp_path / "other"
    other.mkdir()
    win.open_folder(other)  # cancels and joins the worker before swapping libraries
    assert not win.is_indexing
    assert win.doc_tree.topLevelItemCount() == 0
    win.close()
