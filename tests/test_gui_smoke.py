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
    _wait_for_pages(qapp, win)
    assert win.pages_pending() == 0
    assert all(c.decoded for c in win._canvases)

    # force a render pass of the first page to catch painting errors
    from PySide6.QtGui import QPixmap

    pixmap = win._canvases[0].grab()
    assert not pixmap.isNull()
    win.close()


def _wait_for_pages(qapp, win, seconds=20):
    import time

    deadline = time.monotonic() + seconds
    while (win.is_decoding or win.pages_pending()) and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def test_pages_decode_on_a_worker_and_fill_placeholders(qapp, backup_root, monkeypatch):
    from remarksync.backup import DocItem
    from remarksync.gui import MainWindow

    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    win = MainWindow()
    win.open_folder(backup_root)
    doc = next(d for d in win._library.documents if d.name == "Ink Notes")
    win.show_doc(doc, page_no=1)
    # placeholders exist immediately, sized like a page
    assert len(win._canvases) == 2
    assert win._canvases[0].width() > 0
    _wait_for_pages(qapp, win)
    assert win.pages_pending() == 0
    assert win._canvases[0].frame().page.strokes  # the real ink arrived
    # the ink page has content; its frame is not the placeholder's empty page
    assert not win.is_decoding

    # switching documents while decoding cancels cleanly and reuses nothing
    pdf_doc = next(d for d in win._library.documents if d.name == "Paper PDF")
    win.show_doc(pdf_doc)
    win.show_doc(doc)
    _wait_for_pages(qapp, win)
    assert all(c.decoded for c in win._canvases)
    assert all(c.page_index == i for i, c in enumerate(win._canvases))
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


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Tests never read or write the user's real RemarkSync settings: the
    window gets a settings class backed by an ini file in tmp_path."""
    import remarksync.gui as gui
    from PySide6.QtCore import QSettings

    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(gui, "QSettings", lambda *_args: QSettings(str(ini), QSettings.IniFormat))
    yield


def test_read_handwriting_toggle_wires_the_engine_into_indexing(qapp, backup_root, monkeypatch):
    """The toolbar toggle is remembered and, when on, indexing reads ink
    with the handwriting engine (a fake TrOCR here)."""
    import time

    import remarksync.recognize as rec
    from remarksync.gui import MainWindow

    class FakeModel:
        def read_lines(self, images):
            return [("zebra crossing", []) for _ in images]

    monkeypatch.setitem(rec.ENGINES, rec.ENGINE_TROCR,
                        (lambda: True, lambda cache: rec.TrOCRExtractor(cache, model=FakeModel(), min_strokes=1)))
    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    win = MainWindow()
    win.open_folder(backup_root)
    assert not win.handwriting_action.isChecked()
    assert win.handwriting_action.text() == "Read Handwriting: Off"
    win.handwriting_action.setChecked(True)
    assert win.handwriting_action.text() == "Read Handwriting: On"
    win.update_index()
    deadline = time.monotonic() + 20
    while win.is_indexing and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert win._library.handwriting == rec.ENGINE_TROCR
    assert "handwriting: 3 pages read with trocr-large-handwritten, 3 cached" in win.statusBar().currentMessage()
    win.search_box.setText("zebra")
    win.run_search()
    assert win.results_list.count() >= 1
    win.close()

    win2 = MainWindow()  # the choice is remembered
    assert win2.handwriting_action.isChecked()
    win2.close()
