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
    from PySide6.QtWidgets import QLineEdit
    from remarksync import cloud_dialog

    class Unpaired:
        paired = False
    monkeypatch.setattr(cloud_dialog, "CloudClient", Unpaired)
    dialog = cloud_dialog.CloudTrialDialog(destination=tmp_path)
    assert dialog.code.echoMode() == QLineEdit.Password
    assert not dialog.download.isEnabled() and not dialog.open_pdf.isEnabled()
    dialog.start({"operation": "invalid-read-only-operation"})
    assert dialog.process is not None and not dialog.pair.isEnabled()
    dialog.close()
    assert dialog.process is not None  # closing cannot abandon a running operation
    deadline = time.monotonic() + 10
    while dialog.process is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert dialog.process is None
    assert dialog.status.text() == "Unsupported cloud operation."
    assert dialog.pair.isEnabled() and not dialog.open_pdf.isEnabled()
    dialog.close()


def test_tablet_note_layout_is_default(qapp, monkeypatch):
    from remarksync.gui import MainWindow
    monkeypatch.setattr(MainWindow, "_restore_state", lambda self: None)
    window = MainWindow()
    assert not window.pdf_column_action.isChecked()
    window.close()


def test_cloud_pairing_button_opens_browser_connect_route(qapp, tmp_path, monkeypatch):
    from remarksync import cloud_dialog
    class Unpaired:
        paired = False
    opened = []
    monkeypatch.setattr(cloud_dialog, "CloudClient", Unpaired)
    monkeypatch.setattr(cloud_dialog.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    dialog = cloud_dialog.CloudTrialDialog(destination=tmp_path)
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
    class Paired:
        paired = True
    monkeypatch.setattr(cloudsync_dialog, "CloudClient", Paired)
    settings = QSettings(str(tmp_path / "cloud.ini"), QSettings.IniFormat)
    dialog = cloudsync_dialog.CloudSyncDialog(destination=tmp_path / "sync", settings=settings, automatic=False)
    class Process:
        data = b'{"event":"progress","done":2,"total":5,"na'
        def readAllStandardOutput(self):
            data, self.data = self.data, b""
            return data
        def deleteLater(self):
            pass
    process = Process()
    dialog.process = process
    dialog.read_output(process)
    assert dialog.result is None
    process.data = b'me":"Notebook"}\n' + json.dumps({"error": "Network unavailable"}).encode() + b"\n"
    dialog.read_output(process)
    assert dialog.status.text() == "3/5: Notebook"
    dialog.auto.setChecked(True)
    dialog.finished_operation(process, 1)
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
