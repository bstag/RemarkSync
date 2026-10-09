"""Preservation regressions found in the October whole-system review."""

import io
import json
import tarfile
from types import SimpleNamespace

import pytest
from rmscene import CrdtId, LwwValue
from rmscene import scene_items as si

from remarksync.backup import iter_docs, scan_backup
from remarksync.layout import PDF_SCALE, lay_out
from remarksync.pull import LocalTablet, _safe_extract, pull
from remarksync.rmdata import PageModel, decode_page
from remarksync.search import SearchIndex

from .conftest import INK_UUID, PDF_UUID, page_rm, write_content


def edit_tablet(root):
    path = root / f"{INK_UUID}.metadata"
    meta = json.loads(path.read_text())
    meta['lastModified'] = '1800000000000'
    path.write_text(json.dumps(meta))


def test_incomplete_batch_preserves_existing_documents(backup_root, tmp_path):
    dest = tmp_path / 'backup'
    pull(dest, LocalTablet(backup_root))
    old = (dest / f'{INK_UUID}.metadata').read_bytes()
    edit_tablet(backup_root)

    class Incomplete(LocalTablet):
        def fetch(self, uuids, into):
            pass

    with pytest.raises(ValueError, match='incomplete|missing'):
        pull(dest, Incomplete(backup_root))
    assert (dest / f'{INK_UUID}.metadata').read_bytes() == old
    assert (dest / INK_UUID / '0' / '0.rm').exists()


def test_publish_failure_rolls_back_and_next_pull_succeeds(backup_root, tmp_path, monkeypatch):
    import remarksync.pull as module

    dest = tmp_path / 'backup'
    pull(dest, LocalTablet(backup_root))
    old = (dest / f'{INK_UUID}.metadata').read_bytes()
    edit_tablet(backup_root)
    original_move = module._move_document

    def fail(src, target, uuid):
        entries = module._document_entries(src, uuid)
        entries[0].rename(target / entries[0].name)
        raise OSError('simulated full disk')

    monkeypatch.setattr(module, '_move_document', fail)
    with pytest.raises(OSError, match='full disk'):
        pull(dest, LocalTablet(backup_root))
    assert (dest / f'{INK_UUID}.metadata').read_bytes() == old
    assert (dest / INK_UUID / '0' / '0.rm').exists()
    monkeypatch.setattr(module, '_move_document', original_move)
    assert pull(dest, LocalTablet(backup_root)).completed


def test_tar_link_rejected_before_any_member_is_written(tmp_path):
    victim = tmp_path / 'victim.txt'
    victim.write_text('original')
    into = tmp_path / 'staging'
    into.mkdir()
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as tar:
        ordinary = tarfile.TarInfo('ordinary.txt')
        ordinary.size = 4
        tar.addfile(ordinary, io.BytesIO(b'fine'))
        link = tarfile.TarInfo('inside.txt')
        link.type = tarfile.LNKTYPE
        link.linkname = str(victim)
        tar.addfile(link)
    data.seek(0)
    with tarfile.open(fileobj=data, mode='r:') as tar:
        with pytest.raises(ValueError, match='link|type'):
            _safe_extract(tar, into)
    assert victim.read_text() == 'original'
    assert not list(into.iterdir())


def test_modern_pdf_mapping_deleted_and_inserted_pages(backup_root):
    write_content(backup_root, PDF_UUID, {'fileType': 'pdf', 'cPages': {'pages': [
        {'id': '1', 'idx': {'value': 'bc'}, 'redir': {'value': 0}},
        {'id': 'gone', 'idx': {'value': 'ba'}, 'deleted': {'value': 1}, 'redir': {'value': 0}},
        {'id': '0', 'idx': {'value': 'ba'}, 'redir': {'value': 1}},
        {'id': 'notes', 'idx': {'value': 'bb'}, 'template': {'value': 'Lines'}},
    ]}})
    doc = next(d for d in iter_docs(scan_backup(backup_root)) if d.uuid == PDF_UUID)
    assert [p.page_id for p in doc.pages] == ['0', 'notes', '1']
    assert [p.base_index for p in doc.pages] == [1, None, 0]
    assert [p.index for p in doc.pages] == [0, 1, 2]
    assert doc.pages[1].template == 'ruled'
    assert doc.pages[0].rm_path.name == '0.rm'


def test_legacy_pdf_mapping_does_not_use_ui_position(backup_root):
    write_content(backup_root, PDF_UUID, {'fileType': 'pdf', 'pages': ['0', '1'], 'redirectionPageMap': [1, -1]})
    doc = next(d for d in iter_docs(scan_backup(backup_root)) if d.uuid == PDF_UUID)
    assert [p.base_index for p in doc.pages] == [1, None]


def test_deleted_modern_pages_do_not_reappear_as_pdf_fallback(backup_root):
    write_content(backup_root, PDF_UUID, {'fileType': 'pdf', 'cPages': {'pages': [
        {'id': '0', 'deleted': {'value': 1}, 'redir': {'value': 0}},
    ]}})
    docs = list(iter_docs(scan_backup(backup_root)))
    assert next(d for d in docs if d.uuid == PDF_UUID).pages == []


def test_failed_index_keeps_previous_text_and_retries_repaired_page(backup_root, tmp_path):
    index = SearchIndex(tmp_path / 'index.db')
    docs = lambda: list(iter_docs(scan_backup(backup_root)))
    index.refresh(docs())
    rm = backup_root / INK_UUID / '0' / '0.rm'
    rm.write_bytes(b'not a scene')
    edit_tablet(backup_root)
    failed = index.refresh(docs())
    assert failed.failed == 1
    assert index.query('quantum')  # the last good index survived
    rm.write_bytes(page_rm(text='restored preservation note'))
    report = index.refresh(docs())
    assert report.failed == 0
    assert index.query('preservation')


def test_index_detects_scene_edit_without_metadata_change(backup_root, tmp_path):
    index = SearchIndex(tmp_path / 'index.db')
    docs = lambda: list(iter_docs(scan_backup(backup_root)))
    index.refresh(docs())
    (backup_root / INK_UUID / '0' / '0.rm').write_bytes(page_rm(text='changed without metadata'))
    index.refresh(docs())
    assert index.query('changed')


def test_missing_scene_returns_actionable_error(tmp_path):
    model = decode_page(tmp_path / 'missing.rm')
    assert model.error and 'missing.rm' in model.error


def test_green_snapped_highlight_keeps_color():
    glyph = si.GlyphRange(start=0, length=4, text='note', color=si.PenColor.GREEN,
                         rectangles=[si.Rectangle(x=-100, y=200, w=100, h=30)])
    model = decode_page(page_rm(text='hello', glyphs=[glyph]))
    assert model.highlights[0].color == '#00a651'


def test_hidden_anchored_highlight_keeps_visibility_and_translation(monkeypatch):
    import remarksync.rmdata as module

    glyph = si.GlyphRange(start=0, length=4, text='note', color=si.PenColor.GREEN,
                         rectangles=[si.Rectangle(x=-100, y=200, w=100, h=30)])
    group = si.Group(node_id=CrdtId(1, 2), visible=LwwValue(CrdtId(1, 1), False),
                     anchor_id=LwwValue(CrdtId(1, 1), CrdtId(1, 9)),
                     anchor_origin_x=LwwValue(CrdtId(1, 1), 20.0))
    group.children = {'glyph': glyph}
    monkeypatch.setattr(module, '_anchor_positions', lambda text: {CrdtId(1, 9): 50.0})
    monkeypatch.setattr(module, 'read_tree', lambda stream: SimpleNamespace(root=group, root_text=None, scene_info=None))
    model = decode_page(b'fake stream')
    assert not model.highlights[0].layer_visible
    assert model.highlights[0].rectangles == [(622.0, 250.0, 100, 30)]


def test_pdf_center_uses_declared_paper_width():
    # An annotation at the declared sheet centre lies at the PDF centre.
    from remarksync.rmdata import Stroke

    page = PageModel(paper_size=(1620, 2160), strokes=[Stroke([(810, 100), (810, 110)], '#000000', 2)])
    frame = lay_out(page, base_size=(612, 792))
    assert frame.page.strokes[0].points[0][0] == pytest.approx(612 * PDF_SCALE / 2)


def test_second_paper_pro_page_retains_template():
    assert lay_out(PageModel(paper_size=(1620, 2160)), template='ruled', page_index=1).template == 'ruled'


def test_partial_payload_manifest_preserves_existing_ink(backup_root, tmp_path):
    dest = tmp_path / 'backup'
    pull(dest, LocalTablet(backup_root))
    old = (dest / INK_UUID / '0' / '0.rm').read_bytes()
    edit_tablet(backup_root)

    class MissingInk(LocalTablet):
        def fetch(self, uuids, into):
            manifest = super().fetch(uuids, into)
            (into / INK_UUID / '0' / '0.rm').unlink()
            return manifest

    with pytest.raises(ValueError, match='manifest'):
        pull(dest, MissingInk(backup_root))
    assert (dest / INK_UUID / '0' / '0.rm').read_bytes() == old


def test_interrupted_publication_is_recovered_before_planning(backup_root, tmp_path, monkeypatch):
    import remarksync.pull as module
    from remarksync.backupstore import TRANSACTION_DIR

    dest = tmp_path / 'backup'
    pull(dest, LocalTablet(backup_root))
    old = (dest / f'{INK_UUID}.metadata').read_bytes()
    edit_tablet(backup_root)
    original_move = module._move_document
    import remarksync.backupstore as store
    original_recover = store.recover

    def fail(src, target, uuid):
        raise OSError('interrupted publication')

    # Simulate a process dying before it gets a chance to roll back.
    monkeypatch.setattr(module, '_move_document', fail)
    monkeypatch.setattr(store, 'recover', lambda dest: None)
    with pytest.raises(OSError):
        pull(dest, LocalTablet(backup_root))
    assert list((dest / TRANSACTION_DIR).glob('*.json'))
    monkeypatch.setattr(store, 'recover', original_recover)
    monkeypatch.setattr(module, '_move_document', original_move)
    class ChecksRecovery(LocalTablet):
        def read_signatures(self):
            assert (dest / f'{INK_UUID}.metadata').read_bytes() == old
            return super().read_signatures()

    report = pull(dest, ChecksRecovery(backup_root))
    assert INK_UUID in report.changed
    assert (dest / f'{INK_UUID}.metadata').read_bytes() != old
    assert (dest / INK_UUID / '0' / '0.rm').exists()
    assert not (dest / TRANSACTION_DIR).exists()


def test_pull_history_retains_replaced_and_pruned_raw_documents(backup_root, tmp_path):
    from remarksync.backupstore import HISTORY_DIR
    from .conftest import DIARY_UUID

    dest = tmp_path / 'backup'
    pull(dest, LocalTablet(backup_root))
    old = (dest / INK_UUID / '0' / '0.rm').read_bytes()
    edit_tablet(backup_root)
    (backup_root / INK_UUID / '0' / '0.rm').write_bytes(page_rm(text='updated'))
    (backup_root / f'{DIARY_UUID}.metadata').unlink()
    pull(dest, LocalTablet(backup_root), prune=True)
    revisions = list((dest / HISTORY_DIR / INK_UUID).glob('*/' + INK_UUID + '/0/0.rm'))
    assert any(p.read_bytes() == old for p in revisions)
    assert list((dest / HISTORY_DIR / DIARY_UUID).glob('*/' + DIARY_UUID + '.metadata'))
    assert not (dest / f'{DIARY_UUID}.metadata').exists()


def test_second_writer_is_rejected_and_lock_can_be_reused(backup_root, tmp_path):
    from remarksync.backupstore import writer_lock
    dest = tmp_path / 'backup'
    dest.mkdir()
    with writer_lock(dest):
        with pytest.raises(RuntimeError, match='another pull'):
            pull(dest, LocalTablet(backup_root))
    assert pull(dest, LocalTablet(backup_root)).completed


def test_modern_inserted_page_never_steals_numbered_ink(backup_root):
    write_content(backup_root, PDF_UUID, {'fileType': 'pdf', 'cPages': {'pages': [
        {'id': 'notes'}, {'id': '0', 'redir': {'value': 0}},
    ]}})
    doc = next(d for d in iter_docs(scan_backup(backup_root)) if d.uuid == PDF_UUID)
    assert doc.pages[0].rm_path is None
    assert doc.pages[1].rm_path.name == '0.rm'


def test_force_rebuild_retains_good_index_for_unreadable_document(backup_root, tmp_path):
    index = SearchIndex(tmp_path / 'index.db')
    docs = lambda: list(iter_docs(scan_backup(backup_root)))
    index.refresh(docs())
    (backup_root / INK_UUID / '0' / '0.rm').write_bytes(b'not a scene')
    report = index.refresh(docs(), force=True)
    assert report.failed == 1
    assert index.query('quantum')


def test_oversized_tar_refused_before_extraction(tmp_path):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as tar:
        tar.addfile(tarfile.TarInfo('ordinary.txt'), io.BytesIO())
    data.seek(0)
    with tarfile.open(fileobj=data, mode='r:') as tar:
        tar.getmembers()[0].size = 9 * 1024**3
        with pytest.raises(ValueError, match='limits'):
            _safe_extract(tar, tmp_path / 'into')
    assert not (tmp_path / 'into').exists()


def test_dry_run_does_not_create_a_destination(backup_root, tmp_path):
    dest = tmp_path / 'new-backup'
    report = pull(dest, LocalTablet(backup_root), dry_run=True)
    assert report.new
    assert not dest.exists()


def test_archive_never_clears_an_existing_destination(tmp_path):
    import zipfile
    from remarksync.backup import open_rmdoc
    archive = tmp_path / 'notes.rmdoc'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('doc.metadata', '{}')
    dest = tmp_path / 'existing'
    dest.mkdir()
    original = dest / 'keep.txt'
    original.write_text('keep')
    with pytest.raises(ValueError, match='empty destination'):
        open_rmdoc(archive, extract_dir=dest)
    assert original.read_text() == 'keep'
