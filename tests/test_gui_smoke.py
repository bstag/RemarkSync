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


@pytest.fixture(autouse=True)
def isolated_desktop_settings(tmp_path, monkeypatch):
    # Real users can have automatic network sync enabled. GUI tests must never
    # poll their server or change their selected library/preferences.
    from PySide6.QtCore import QSettings
    from remarksync import gui, cloudsync_dialog
    def settings(_organization, application):
        return QSettings(str(tmp_path / (application + '.ini')), QSettings.IniFormat)
    monkeypatch.setattr(gui, 'QSettings', settings)
    monkeypatch.setattr(cloudsync_dialog, 'QSettings', settings)


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


def test_cloud_dialog_masked_input_and_worker_failure(qapp, tmp_path, monkeypatch):
    import time
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QLineEdit
    from remarksync import cloudsync_dialog

    class Unpaired:
        paired = False
    monkeypatch.setattr(cloudsync_dialog, "CloudClient", Unpaired)
    settings = QSettings(str(tmp_path / "cloud.ini"), QSettings.IniFormat)
    dialog = cloudsync_dialog.CloudSyncDialog(destination=tmp_path / "sync", settings=settings, automatic=False)
    assert dialog.code.echoMode() == QLineEdit.Password
    assert not dialog.open_library.isEnabled() and not dialog.open_exports.isEnabled()
    dialog.start({"operation": "invalid-read-only-operation"})  # a real worker process, through the shared launcher
    assert dialog.process is not None and not dialog.pair.isEnabled()
    dialog.close()
    assert dialog.process is not None  # closing cannot abandon a running operation
    deadline = time.monotonic() + 20
    while dialog.process is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert dialog.process is None
    assert dialog.status.text() == "Unsupported cloud operation."
    assert dialog.pair.isEnabled() and not dialog.open_exports.isEnabled() and dialog.failures == 1
    dialog.close()


def test_worker_that_cannot_start_reports_once_after_the_caller_has_the_process(qapp, monkeypatch):
    import time
    from remarksync import workerprocess

    monkeypatch.setattr(workerprocess.sys, "executable", "no-such-python-interpreter")
    answers = []
    process = workerprocess.start_json_worker(None, "remarksync.cloud_worker", {"operation": "pair"},
                                              lambda *answer: answers.append(answer))
    assert answers == []  # never from inside the call: the caller must be able to store the process first
    deadline = time.monotonic() + 10
    while not answers and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert answers == [(process, None, 1)]


def test_saved_sync_destination_is_kept_unless_a_path_is_given(qapp, tmp_path, monkeypatch):
    """No destination means "not given" (the CLI without --dest): the saved folder stays."""
    from PySide6.QtCore import QSettings
    from remarksync import cloudsync_dialog

    class Paired:
        paired = True
    monkeypatch.setattr(cloudsync_dialog, "CloudClient", Paired)
    settings = QSettings(str(tmp_path / "cloud.ini"), QSettings.IniFormat)
    saved, given = (tmp_path / "saved").resolve(), (tmp_path / "given").resolve()
    settings.setValue("destination", str(saved))
    dialog = cloudsync_dialog.CloudSyncDialog(settings=settings, automatic=False, source="cloud")
    assert dialog.destination == saved
    dialog.close()
    dialog = cloudsync_dialog.CloudSyncDialog(destination=None, settings=settings, automatic=False, source="cloud")
    assert dialog.destination == saved and settings.value("destination") == str(saved)
    dialog.close()
    dialog = cloudsync_dialog.CloudSyncDialog(destination=given, settings=settings, automatic=False, source="cloud")
    assert dialog.destination == given and settings.value("destination") == str(given)
    dialog.close()
    # a path that happens to be the default is still a path that was given
    dialog = cloudsync_dialog.CloudSyncDialog(destination=cloudsync_dialog.DEFAULT_DESTINATION, settings=settings,
                                            automatic=False, source="cloud")
    assert dialog.destination == cloudsync_dialog.DEFAULT_DESTINATION.resolve()
    dialog.close()


def test_tablet_note_layout_is_default(qapp, monkeypatch):
    from remarksync.gui import MainWindow
    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    window = MainWindow()
    assert not window.pdf_column_action.isChecked()
    window.close()


def test_cloud_pairing_button_opens_browser_connect_route(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from remarksync import cloudsync_dialog
    class Unpaired:
        paired = False
    opened = []
    monkeypatch.setattr(cloudsync_dialog, "CloudClient", Unpaired)
    monkeypatch.setattr(cloudsync_dialog.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    settings = QSettings(str(tmp_path / "cloud.ini"), QSettings.IniFormat)
    dialog = cloudsync_dialog.CloudSyncDialog(destination=tmp_path / "sync", settings=settings, automatic=False)
    dialog.login.click()
    assert opened == ["https://my.remarkable.com/device/browser/connect"]
    dialog.close()


def test_cloud_automatic_timer_prevents_overlap_and_can_stop(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from remarksync import cloudsync_dialog
    class Paired:
        paired = True
    monkeypatch.setattr(cloudsync_dialog, "CloudClient", Paired)
    settings = QSettings(str(tmp_path / "cloud.ini"), QSettings.IniFormat)
    dialog = cloudsync_dialog.CloudSyncDialog(destination=tmp_path / "sync", settings=settings, automatic=False)
    started = []
    def start(request):
        started.append(request)
        dialog.process = object()
    monkeypatch.setattr(dialog, "start", start)
    dialog.auto.setChecked(True)
    qapp.processEvents()
    assert len(started) == 1 and started[0]["operation"] == "sync" and not started[0]["pdfs_only"]
    dialog.sync_now()
    dialog.timer.timeout.emit()
    assert len(started) == 1
    dialog.auto.setChecked(False)
    assert not dialog.timer.isActive() and not settings.value("automatic", True, type=bool)
    assert dialog.process is not None  # stopping polling lets acquisition finish
    dialog.process = None
    dialog.close()


def test_cloud_progress_chunks_and_failure_backoff(qapp, tmp_path, monkeypatch):
    import json
    from PySide6.QtCore import QSettings
    from remarksync import cloudsync_dialog
    from remarksync.workerprocess import Reply
    class Paired:
        paired = True
    monkeypatch.setattr(cloudsync_dialog, "CloudClient", Paired)
    settings = QSettings(str(tmp_path / "cloud.ini"), QSettings.IniFormat)
    dialog = cloudsync_dialog.CloudSyncDialog(destination=tmp_path / "sync", settings=settings, automatic=False)
    process = object()
    dialog.process = process
    reply = Reply(dialog.progress_event)
    reply.feed(b'{"event":"progress","done":2,"total":5,"na')
    assert reply.result is None and dialog.status.text() != "3/5: Notebook"
    reply.feed(b'me":"Notebook"}\nnot json\n[1]\n' + json.dumps({"error": "Network unavailable"}).encode())
    assert dialog.status.text() == "3/5: Notebook" and reply.result is None
    reply.feed(b"", last=True)  # the result line had no newline; the end of output completes it
    assert reply.result == {"error": "Network unavailable"}
    dialog.auto.setChecked(True)
    dialog.finished_operation(process, reply.result, 1)
    assert dialog.status.text() == "Network unavailable"
    assert dialog.process is None and dialog.failures == 1
    assert dialog.timer.isActive() and dialog.timer.interval() == 600_000
    dialog.auto.setChecked(False)
    dialog.close()


def test_cloud_library_uses_same_polling_controller(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from remarksync import cloudsync_dialog
    class Paired:
        paired = True
    monkeypatch.setattr(cloudsync_dialog, "CloudClient", Paired)
    settings = QSettings(str(tmp_path / "cloud.ini"), QSettings.IniFormat)
    destination = tmp_path / "sync"
    (destination / "raw").mkdir(parents=True)
    dialog = cloudsync_dialog.CloudSyncDialog(destination=destination, settings=settings, automatic=False)
    dialog.show_library()
    assert dialog.library_window._cloud_dialog is dialog
    assert dialog.parent() is dialog.library_window
    assert dialog.library_window._library.root == destination / "raw"
    dialog.library_window.close()


def test_central_sync_source_avoids_pairing_and_keeps_destinations_separate(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from remarksync import cloudsync_dialog
    def forbidden():
        raise AssertionError('Central sync must not load cloud credentials')
    monkeypatch.setattr(cloudsync_dialog, 'CloudClient', forbidden)
    settings = QSettings(str(tmp_path / 'central.ini'), QSettings.IniFormat)
    monkeypatch.setenv('REMARKSYNC_SERVER', 'https://server.example.ts.net:8443')
    dialog = cloudsync_dialog.CloudSyncDialog(destination=tmp_path / 'central', settings=settings,
                                            automatic=False, source='server')
    assert dialog.code.isHidden() and dialog.pair.isHidden() and not dialog.server_address.isHidden()
    requests = []
    monkeypatch.setattr(dialog, 'start', requests.append)
    dialog.sync_now()
    assert requests == [{'operation': 'server-sync', 'server': 'https://server.example.ts.net:8443',
                         'destination': str((tmp_path / 'central').resolve())}]
    assert settings.value('source') == 'server' and not settings.value('destination')
    dialog.close()


def test_central_sync_has_no_builtin_server_and_refuses_an_empty_address(qapp, tmp_path, monkeypatch):
    """No installation's address ships in the package; the field starts empty without one."""
    from PySide6.QtCore import QSettings
    from remarksync import cloudsync_dialog

    monkeypatch.delenv('REMARKSYNC_SERVER', raising=False)
    settings = QSettings(str(tmp_path / 'empty.ini'), QSettings.IniFormat)
    dialog = cloudsync_dialog.CloudSyncDialog(destination=tmp_path / 'central', settings=settings,
                                            automatic=False, source='server')
    assert dialog.server_address.text() == ''
    requests = []
    monkeypatch.setattr(dialog, 'start', requests.append)
    dialog.sync_now()
    assert requests == [] and 'address' in dialog.status.text()
    dialog.close()


@pytest.mark.parametrize('open_library', [False, True])
def test_compile_toolbar_saves_archive_with_or_without_library(qapp, backup_root, tmp_path, monkeypatch, open_library):
    from remarksync.gui import MainWindow
    from remarksync.backup import open_rmdoc
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(MainWindow, '_restore_state', lambda self: None)
    src, out = tmp_path / 'notes.md', tmp_path / 'notes.rmdoc'
    src.write_text('# Preservation\n\nA note')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(src), ''))
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(out), ''))
    win = MainWindow()
    try:
        if open_library:
            win.open_folder(backup_root)
        win.compile_md_action()
        assert out.exists()
        assert open_rmdoc(out, extract_dir=tmp_path / 'opened').pages
    finally:
        win.close()


def test_export_toolbar_runs_in_separate_process(qapp, backup_root, tmp_path_factory, monkeypatch):
    import time
    import json
    import fitz
    from PySide6.QtWidgets import QFileDialog
    from remarksync.backup import iter_docs, scan_backup
    from remarksync.gui import MainWindow

    out = tmp_path_factory.mktemp("exports") / "gui-preserved.pdf"
    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(out), ""))
    doc = next(d for d in iter_docs(scan_backup(backup_root)) if d.file_type == "pdf")
    from .conftest import page_rm
    doc.pages[0].rm_path.write_bytes(page_rm(text="A typed note that should follow the original PDF body column. " * 6))
    win = MainWindow()
    try:
        win.pdf_column_action.toggled.disconnect()
        win.pdf_column_action.setChecked(True)
        assert not win.export_action.isEnabled()
        win.show_doc(doc)
        assert win.export_action.isEnabled()
        win.export_pdf_action()
        assert win._export_process is not None
        assert not win.export_action.isEnabled()
        deadline = time.monotonic() + 20
        while win._export_process is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert win._export_process is None, win.statusBar().currentMessage()
        assert out.exists(), win.statusBar().currentMessage()
        assert "Exported" in win.statusBar().currentMessage()
        assert win.export_action.isEnabled()
        with fitz.open(out) as pdf:
            assert pdf.embfile_get("original.pdf") == doc.base_pdf.read_bytes()
            manifest = json.loads(pdf.embfile_get("remarksync-manifest.json"))
            assert manifest["text_layout"] == "pdf-column"
            assert manifest["pages"][0]["note_layout"]
    finally:
        if win._export_process is not None:
            win._export_process.kill()
            win._export_process.waitForFinished(3000)
            qapp.processEvents()
        win.close()


def test_export_toolbar_reports_decode_failure(qapp, backup_root, tmp_path_factory, monkeypatch):
    import time
    from PySide6.QtWidgets import QFileDialog
    from remarksync.backup import iter_docs, scan_backup
    from remarksync.gui import MainWindow

    doc = next(d for d in iter_docs(scan_backup(backup_root)) if d.file_type == "pdf")
    doc.pages[0].rm_path.write_bytes(b"broken scene")
    out = tmp_path_factory.mktemp("exports") / "failed.pdf"
    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(out), ""))
    win = MainWindow()
    try:
        win.show_doc(doc)
        win.export_pdf_action()
        deadline = time.monotonic() + 20
        while win._export_process is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert "PDF export failed" in win.statusBar().currentMessage()
        assert not out.exists()
    finally:
        if win._export_process is not None:
            win._export_process.kill()
            win._export_process.waitForFinished(3000)
            qapp.processEvents()
        win.close()


def test_decode_worker_does_not_open_pdf(qapp, backup_root, monkeypatch):
    import remarksync.gui as gui
    from remarksync.backup import iter_docs, scan_backup

    doc = next(d for d in iter_docs(scan_backup(backup_root)) if d.file_type == 'pdf')
    def refuse(*a, **k):
        raise AssertionError('PDF touched from decoding worker')
    monkeypatch.setattr(gui, 'PdfUnderlay', refuse)
    worker = gui.DecodeWorker(doc, base_sizes={0: (595, 842), 1: (595, 842)})
    frames = []
    worker.decoded.connect(lambda index, frame: frames.append(frame))
    worker.run()
    assert len(frames) == len(doc.pages)
    assert all(f.underlay is not None for f in frames)


def test_unreadable_page_is_visible_in_gui(qapp, backup_root, monkeypatch):
    from remarksync.gui import MainWindow
    monkeypatch.setattr(MainWindow, '_restore_state', lambda self: None)
    doc_path = backup_root / 'bbbbbbbb-1111-2222-3333-444444444444' / '0' / '0.rm'
    doc_path.write_bytes(b'not a scene')
    win = MainWindow()
    try:
        win.open_folder(backup_root)
        win.show_doc(next(d for d in win._library.documents if d.name == 'Ink Notes'))
        _wait_for_pages(qapp, win)
        assert 'not a readable' in win._canvases[0].toolTip()
        assert 'unreadable' in win.statusBar().currentMessage().lower()
    finally:
        win.close()


def test_pdf_underlay_uses_source_mapping_and_ignores_inserted_page(qapp, backup_root, monkeypatch):
    import remarksync.viewer as viewer
    from remarksync.backup import iter_docs, scan_backup
    from .conftest import PDF_UUID, write_content

    write_content(backup_root, PDF_UUID, {'fileType': 'pdf', 'cPages': {'pages': [
        {'id': '0', 'redir': {'value': 1}}, {'id': 'notes'},
    ]}})
    doc = next(d for d in iter_docs(scan_backup(backup_root)) if d.uuid == PDF_UUID)
    with_pdf = viewer.PdfUnderlay(doc.base_pdf)
    requests = []
    real_image = with_pdf.page_image
    monkeypatch.setattr(with_pdf, 'page_image', lambda index, size: requests.append(index) or real_image(index, size))
    try:
        first = viewer.PageCanvas(doc, doc.pages[0], with_pdf)
        inserted = viewer.PageCanvas(doc, doc.pages[1], with_pdf)
        first.grab()
        inserted.grab()
        assert requests == [1]
        assert inserted.frame().underlay is None
        first.deleteLater()
        inserted.deleteLater()
    finally:
        with_pdf.close()


def test_pdf_raster_cache_is_bounded_and_size_aware(qapp, backup_root):
    from remarksync.viewer import PdfUnderlay
    from .conftest import PDF_UUID

    pdf = PdfUnderlay(backup_root / f'{PDF_UUID}.pdf', cache_bytes=2_000_000)
    try:
        small = pdf.page_image(0, (100, 150))
        large = pdf.page_image(0, (200, 300))
        assert large.width() > small.width()
        for i in range(2):
            pdf.page_image(i, (500, 750))
        assert sum(image.sizeInBytes() for image in pdf._cache.values()) <= 2_000_000
        assert pdf.page_image(-1, (100, 150)) is None
    finally:
        pdf.close()


@pytest.mark.parametrize('operation', ['indexing', 'decoding'])
def test_worker_timeout_keeps_running_worker_owned(qapp, monkeypatch, operation):
    from types import SimpleNamespace
    from remarksync.gui import MainWindow
    monkeypatch.setattr(MainWindow, '_restore_state', lambda self: None)
    win = MainWindow()
    prefix = '_index' if operation == 'indexing' else '_decode'
    thread = SimpleNamespace(quit=lambda: None, wait=lambda timeout: False)
    worker = SimpleNamespace(cancel=lambda: None)
    setattr(win, prefix + '_thread', thread)
    setattr(win, prefix + '_worker', worker)
    try:
        stopped = win._stop_indexing() if operation == 'indexing' else win._stop_decoding()
        assert not stopped
        assert getattr(win, prefix + '_thread') is thread
        assert getattr(win, prefix + '_worker') is worker
        assert 'still stopping' in win.statusBar().currentMessage()
    finally:
        setattr(win, prefix + '_thread', None)
        setattr(win, prefix + '_worker', None)
        win.close()


def test_pages_decode_on_a_worker_and_fill_placeholders(qapp, backup_root, monkeypatch):
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


def test_handwriting_toggle_without_an_engine_says_so(qapp, backup_root, monkeypatch):
    """Toggling Read Handwriting on with no engine installed must say so in
    the status bar — in the start message and again when indexing finishes —
    not silently index typed text only."""
    import time

    import remarksync.recognize as rec
    from remarksync.gui import MainWindow

    monkeypatch.setattr(rec, "trocr_available", lambda: False)
    monkeypatch.setattr(rec, "windows_ink_available", lambda: False)
    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    win = MainWindow()
    win.open_folder(backup_root)
    win.handwriting_action.setChecked(True)
    win.update_index()
    assert "no handwriting engine installed" in win.statusBar().currentMessage()
    deadline = time.monotonic() + 20
    while win.is_indexing and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert "Index updated" in win.statusBar().currentMessage()
    assert "no handwriting engine installed" in win.statusBar().currentMessage()
    assert win._library.handwriting is None
    win.close()


def test_edit_dialog_edits_a_draft_and_never_sends_without_confirmation(qapp, backup_root, tmp_path, monkeypatch):
    import zipfile

    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
    from remarksync import edit_dialog
    from remarksync.backup import iter_docs, scan_backup
    from remarksync.rmedit import Draft
    from .conftest import JOURNAL_UUID, write_cpages_doc

    pages = ["11111111-aaaa-4bbb-8ccc-00000000000%d" % i for i in range(3)]
    write_cpages_doc(backup_root, JOURNAL_UUID, "2026", "", pages)
    doc = next(d for d in iter_docs(scan_backup(backup_root)) if d.uuid == JOURNAL_UUID)
    sends = []
    monkeypatch.setattr(edit_dialog.Sender, "send", lambda self, archive, name, **kw: sends.append((archive, name, kw)))
    dialog = edit_dialog.EditDialog(Draft.from_document(doc), sent_root=tmp_path / "sent")
    assert dialog.page_list.count() == 3 and not dialog.replace_button.isEnabled()

    dialog.page_list.setCurrentRow(0)
    dialog.editor.setPlainText("# Coffee log\njournal page 0 entry about coffee")
    assert dialog.draft.pages[0].changed and dialog.replace_button.isEnabled()
    dialog._move(1)
    dialog._duplicate()
    assert dialog.page_list.count() == 4 and dialog.page_list.currentRow() == 2

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Cancel)
    dialog.send_replace()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Copy", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    dialog.send_copy()
    assert sends == [] and not (tmp_path / "sent").exists()  # declined: nothing built, nothing sent

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Yes)
    dialog.send_replace()
    archive, name, options = sends[0]
    assert name == "2026" and options["replace"] == dialog.draft.base_files and archive.is_file()
    with zipfile.ZipFile(archive) as bundle:
        assert JOURNAL_UUID + ".content" in bundle.namelist()  # a replacement keeps the document's identity
    dialog._sent({"status": "sent", "name": "2026", "replaced": True, "kept": "somewhere"})
    assert not dialog.replace_button.isEnabled() and "Replaced" in dialog.status.text()

    out = tmp_path / "saved.rmdoc"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    dialog.save_as()
    saved = Draft.from_archive(out)
    assert saved.identifier != JOURNAL_UUID and saved.pages[1].text.startswith("# Coffee log")
    original = Draft.from_document(next(d for d in iter_docs(scan_backup(backup_root)) if d.uuid == JOURNAL_UUID))
    assert not original.pages[0].text.startswith("#")  # the library copy was never written


def test_edit_action_is_for_notebooks_and_send_results_read_plainly(qapp, backup_root, monkeypatch):
    from remarksync import edit_dialog
    from remarksync.backup import iter_docs, scan_backup
    from remarksync.gui import MainWindow
    from .conftest import JOURNAL_UUID, PDF_UUID

    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    win = MainWindow()
    win.open_folder(backup_root)
    docs = {d.uuid: d for d in iter_docs(scan_backup(backup_root))}
    assert not win.edit_action.isEnabled()
    win.show_doc(docs[PDF_UUID])
    assert not win.edit_action.isEnabled()
    win.show_doc(docs[JOURNAL_UUID])
    assert win.edit_action.isEnabled()
    win.edit_doc_action()
    assert win._edit_dialogs[-1].draft.identifier == JOURNAL_UUID
    win._edit_dialogs[-1].close()
    _wait_for_pages(qapp, win)
    assert edit_dialog.describe({"error": "x"}) == "Not sent: x"
    assert "nothing was sent" in edit_dialog.describe({"status": "already-sent", "name": "n"})
    assert "next sync" in edit_dialog.describe({"status": "sent", "name": "n", "replaced": False})
    win.close()


def test_documents_removed_from_the_tablet_are_hidden_until_asked_for(qapp, backup_root, tmp_path, monkeypatch):
    import json
    import shutil

    from remarksync.gui import MainWindow
    from .conftest import DIARY_UUID, FOLDER_UUID, JOURNAL_UUID

    raw = tmp_path / "store" / "raw"
    shutil.copytree(backup_root, raw, ignore=shutil.ignore_patterns("store"))
    (raw.parent / "sync-state.json").write_text(json.dumps({"documents": {
        DIARY_UUID: {"absent_since": "2026-11-21T10:00:00Z"}, JOURNAL_UUID: {"absent_since": "2026-11-20T09:00:00Z"},
        FOLDER_UUID: {}}}), encoding="utf-8")
    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    win = MainWindow()
    win.open_folder(raw)

    def labels(node=None):
        node = node or win.doc_tree.invisibleRootItem()
        return [node.child(i).text(0) for i in range(node.childCount())] + [
            text for i in range(node.childCount()) for text in labels(node.child(i))]

    assert not any("Diary 2026" in text or "🗎 2026" in text or "Removed" in text for text in labels())
    assert any("Ink Notes" in text for text in labels()) and "2 removed from the tablet are kept" in win.statusBar().currentMessage()
    win.removed_action.setChecked(True)
    group = win.doc_tree.topLevelItem(win.doc_tree.topLevelItemCount() - 1)
    assert group.text(0) == "🗑 Removed from tablet (2)" and not group.isExpanded()
    removed = [group.child(i).text(0) for i in range(group.childCount())]
    assert removed[0].startswith("🗎 2026") and removed[0].endswith("— was in Research") and "Diary 2026" in removed[1]
    win._on_tree_click(group.child(1), 0)  # a removed document still opens
    assert win._active_doc.uuid == DIARY_UUID
    _wait_for_pages(qapp, win)
    win.removed_action.setChecked(False)
    assert not any("Removed" in text for text in labels())
    win.close()


def test_edit_dialog_cannot_be_dismissed_while_sending_or_lose_edits_unasked(qapp, backup_root, tmp_path, monkeypatch):
    """Esc reaches a dialog as reject(), with no close event. It must not hide
    a dialog that is still sending, nor drop edits without asking."""
    from PySide6.QtWidgets import QMessageBox
    from remarksync import edit_dialog
    from remarksync.backup import iter_docs, scan_backup
    from remarksync.rmedit import Draft
    from .conftest import JOURNAL_UUID, write_cpages_doc

    write_cpages_doc(backup_root, JOURNAL_UUID, "2026", "", ["11111111-aaaa-4bbb-8ccc-00000000000%d" % i for i in range(2)])
    doc = next(d for d in iter_docs(scan_backup(backup_root)) if d.uuid == JOURNAL_UUID)
    asked = []
    answer = [QMessageBox.No]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[1]) or answer[0])
    dialog = edit_dialog.EditDialog(Draft.from_document(doc), sent_root=tmp_path / "sent")
    dialog.show()

    dialog.sender_.process = object()  # a send is running
    dialog.reject()
    assert dialog.isVisible() and "Wait for the send" in dialog.status.text()
    assert not dialog.close() and dialog.isVisible() and asked == []
    dialog.hide()
    assert not dialog.close()  # nor may a hidden one be closed under a running send
    dialog.show()
    dialog.sender_.process = None

    dialog.page_list.setCurrentRow(0)
    dialog.editor.setPlainText("a new first line")
    dialog.reject()
    assert dialog.isVisible() and asked == ["Discard edits"]  # declined: still open, edits intact
    assert not dialog.close() and dialog.isVisible() and len(asked) == 2
    assert dialog.draft.pages[0].text == "a new first line"

    dialog._sent({"status": "sent", "name": "2026"})  # sent: nothing is left to lose
    dialog.reject()
    assert not dialog.isVisible() and len(asked) == 2

    dialog.show()
    dialog._duplicate()
    answer[0] = QMessageBox.Yes
    dialog.reject()
    assert not dialog.isVisible() and len(asked) == 3


def test_window_does_not_close_under_a_hidden_dialog_that_is_still_sending(qapp, backup_root, monkeypatch):
    from remarksync.backup import iter_docs, scan_backup
    from remarksync.gui import MainWindow
    from .conftest import JOURNAL_UUID

    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    win = MainWindow()
    win.open_folder(backup_root)
    win.show_doc(next(d for d in iter_docs(scan_backup(backup_root)) if d.uuid == JOURNAL_UUID))
    _wait_for_pages(qapp, win)
    win.edit_doc_action()
    sending = win._edit_dialogs[-1]
    sending.hide()
    sending.sender_.process = object()
    assert not win.close() and "still being sent" in win.statusBar().currentMessage()
    win.edit_doc_action()  # opening another editor must not forget the one that is sending
    assert sending in win._edit_dialogs and len(win._edit_dialogs) == 2
    win._edit_dialogs[-1].hide()
    assert not win.close()
    sending.sender_.process = None
    win.edit_doc_action()
    assert len(win._edit_dialogs) == 1  # finished and closed dialogs are let go
    win._edit_dialogs[-1].hide()
    assert win.close()


def test_document_opens_without_its_pdf_when_the_pdf_cannot_be_read(qapp, backup_root, monkeypatch):
    import remarksync.gui as gui
    from .conftest import JOURNAL_UUID, PDF_UUID

    monkeypatch.setattr(gui.MainWindow, "_restore_state", lambda self: None)
    win = gui.MainWindow()
    try:
        win.open_folder(backup_root)
        docs = {d.uuid: d for d in win._library.documents}
        win.show_doc(docs[JOURNAL_UUID])
        _wait_for_pages(qapp, win)
        assert win.edit_action.isEnabled()

        def unreadable(_path):
            raise ValueError("damaged file")
        monkeypatch.setattr(gui, "PdfUnderlay", unreadable)
        win.show_doc(docs[PDF_UUID])
        _wait_for_pages(qapp, win)
        # the window describes the document it shows, not the one before it
        assert win._active_doc is docs[PDF_UUID] and not win.edit_action.isEnabled()
        assert len(win._canvases) == len(docs[PDF_UUID].pages) and win.pages_pending() == 0
        assert "could not open base PDF" in win.statusBar().currentMessage()
        assert "damaged file" in win.statusBar().currentMessage()
    finally:
        win.close()


def test_long_documents_fill_in_with_one_timer_and_index_threads_are_released(qapp, backup_root, monkeypatch):
    import time
    from PySide6.QtCore import QThread, QTimer
    from remarksync.gui import MainWindow
    from .conftest import JOURNAL_UUID, write_cpages_doc

    write_cpages_doc(backup_root, JOURNAL_UUID, "2026", "", ["22222222-aaaa-4bbb-8ccc-%012d" % i for i in range(60)])
    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    win = MainWindow()
    try:
        win.open_folder(backup_root)
        timers = len(win.findChildren(QTimer))
        doc = win._library.document(JOURNAL_UUID)
        for _ in range(3):
            win.show_doc(doc)
            assert len(win._canvases) == 40 and win._fill_timer.isActive()
        deadline = time.monotonic() + 20
        while (win._fill_timer.isActive() or win.is_decoding or win.pages_pending()) and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert len(win._canvases) == 60 and not win._fill_timer.isActive()
        assert len(win.findChildren(QTimer)) == timers

        for _ in range(3):
            win.update_index()
            deadline = time.monotonic() + 20
            while win.is_indexing and time.monotonic() < deadline:
                qapp.processEvents()
                time.sleep(0.01)
            qapp.processEvents()
        qapp.sendPostedEvents(None, 52)  # QEvent.DeferredDelete
        assert len([t for t in win.findChildren(QThread) if t is not win._decode_thread]) == 1
    finally:
        win.close()


def test_keyboard_activation_opens_documents_and_hits_without_reloading_on_double_click(qapp, backup_root, monkeypatch):
    from remarksync.gui import MainWindow
    from remarksync.search import SearchHit
    from .conftest import JOURNAL_UUID

    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    win = MainWindow()
    try:
        win.open_folder(backup_root)
        shown = []
        real = win.show_doc
        monkeypatch.setattr(win, "show_doc", lambda doc, page_no=None: shown.append((doc.uuid, page_no)) or real(doc, page_no))

        def find(node):
            item = node.data(0, 0x0100)
            if getattr(item, "uuid", None) == JOURNAL_UUID and getattr(item, "kind", "") == "doc":
                return node
            return next((found for i in range(node.childCount()) if (found := find(node.child(i)))), None)

        node = find(win.doc_tree.invisibleRootItem())
        win.doc_tree.itemActivated.emit(node, 0)  # Enter on a document that is not open
        assert shown == [(JOURNAL_UUID, None)]
        win.doc_tree.itemClicked.emit(node, 0)  # a double-click: click, then activation
        win.doc_tree.itemActivated.emit(node, 0)
        assert len(shown) == 2

        win.results_list.addItem("hit")
        entry = win.results_list.item(0)
        entry.setData(0x0100, SearchHit(JOURNAL_UUID, "2026", 0, ""))
        win.results_list.itemActivated.emit(entry)
        assert shown[-1] == (JOURNAL_UUID, 0) and len(shown) == 3
        win.results_list.itemClicked.emit(entry)
        win.results_list.itemActivated.emit(entry)
        assert len(shown) == 4
        _wait_for_pages(qapp, win)
    finally:
        win.close()


def test_sync_action_keeps_one_controller_and_close_message_names_the_sync(qapp, tmp_path, monkeypatch):
    from remarksync import cloudsync_dialog
    from remarksync.gui import MainWindow

    class Paired:
        paired = True
    monkeypatch.setattr(cloudsync_dialog, "CloudClient", Paired)
    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    win = MainWindow(cloud_autostart=False)
    assert win._cloud_dialog is None
    win.library_sync_action(show=False)
    dialog = win._cloud_dialog
    win.library_sync_action(show=False)
    assert win._cloud_dialog is dialog
    dialog.process = object()
    assert not win.close() and "library sync" in win.statusBar().currentMessage()
    dialog.process = None
    assert win.close()


@pytest.mark.parametrize('operation', ['indexing', 'decoding'])
def test_a_slow_stop_finishes_in_the_background_and_then_continues(qapp, monkeypatch, operation):
    import threading
    from types import SimpleNamespace

    from PySide6.QtCore import QThread

    from remarksync import gui
    monkeypatch.setattr(gui.MainWindow, '_restore_state', lambda self: None)
    monkeypatch.setattr(gui, 'STOP_WAIT_MS', 10)
    release = threading.Event()

    class Slow(QThread):
        def run(self):
            release.wait(10)

    win = gui.MainWindow()
    prefix = '_index' if operation == 'indexing' else '_decode'
    thread = Slow(win)
    setattr(win, prefix + '_thread', thread)
    setattr(win, prefix + '_worker', SimpleNamespace(cancel=lambda: None))
    stop = win._stop_indexing if operation == 'indexing' else win._stop_decoding
    continued = []
    thread.start()
    try:
        assert not stop(then=lambda: continued.append('first'))
        assert not stop(then=lambda: continued.append('latest'))  # the latest request wins
        assert 'continuing when it finishes' in win.statusBar().currentMessage() and continued == []
        release.set()
        assert thread.wait(5000)
        for _ in range(50):
            qapp.processEvents()
        assert continued == ['latest']
        assert stop() and getattr(win, prefix + '_thread') is None
    finally:
        release.set()
        thread.wait(5000)
        win.close()


def test_pages_are_painted_at_the_displays_pixel_ratio(qapp, backup_root, monkeypatch):
    from remarksync import viewer
    from remarksync.backup import iter_docs, scan_backup

    doc = next(iter_docs(scan_backup(backup_root)))
    canvas = viewer.PageCanvas(doc, doc.pages[0], None, scale=0.25)
    painted = []
    real_put = viewer._painted_pages.put
    monkeypatch.setattr(viewer._painted_pages, 'put', lambda key, pix: painted.append(pix) or real_put(key, pix))
    monkeypatch.setattr(canvas, 'devicePixelRatioF', lambda: 2.0)
    canvas.grab()
    assert painted and painted[-1].devicePixelRatio() == 2.0
    assert painted[-1].width() == canvas.width() * 2 and painted[-1].height() == canvas.height() * 2
    canvas.deleteLater()
