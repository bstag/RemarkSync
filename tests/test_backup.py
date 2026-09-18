import pytest

from remarksync.backup import (TEMPLATE_DOTS, TEMPLATE_RULED, iter_docs,
                                open_rmdoc, scan_backup)

from .conftest import (DELETED_UUID, DIARY_UUID, FOLDER_UUID, INK_UUID,
                       JOURNAL_UUID, PDF_UUID)


def test_scan_builds_tree(backup_root):
    tree = scan_backup(backup_root)
    assert len(tree) == 2  # Research folder + Diary doc (deleted skipped)
    folder = next(t for t in tree if t.kind == "folder")
    assert folder.name == "Research"
    assert [c.name for c in folder.children] == ["2026", "Ink Notes", "Paper PDF"]


def test_scan_skips_deleted(backup_root):
    docs = {d.uuid: d for d in iter_docs(scan_backup(backup_root))}
    assert DELETED_UUID not in docs
    assert set(docs) == {INK_UUID, PDF_UUID, DIARY_UUID, JOURNAL_UUID}


def test_notebook_pages_and_templates(backup_root):
    docs = {d.uuid: d for d in iter_docs(scan_backup(backup_root))}
    ink = docs[INK_UUID]
    assert ink.file_type == "notebook"
    assert len(ink.pages) == 2
    assert ink.pages[0].rm_path is not None and ink.pages[0].rm_path.exists()
    assert ink.pages[0].template == "blank"
    assert ink.pages[1].template == TEMPLATE_DOTS


def test_pdf_doc_has_base(backup_root):
    docs = {d.uuid: d for d in iter_docs(scan_backup(backup_root))}
    pdf_doc = docs[PDF_UUID]
    assert pdf_doc.file_type == "pdf"
    assert pdf_doc.resolve_base_pdf() == backup_root / f"{PDF_UUID}.pdf"
    # page 1 has no annotations: rm_path may be None but the page is kept
    assert len(pdf_doc.pages) == 2


def test_templates_normalised(backup_root):
    docs = {d.uuid: d for d in iter_docs(scan_backup(backup_root))}
    assert docs[DIARY_UUID].pages[0].template == TEMPLATE_RULED


def test_cpages_journal_doc(backup_root):
    """Software 3.x docs: pages as dicts under cPages.pages, flat .rm files,
    per-page template inline — must resolve like any other notebook."""
    docs = {d.uuid: d for d in iter_docs(scan_backup(backup_root))}
    journal = docs[JOURNAL_UUID]
    assert journal.name == "2026"
    assert journal.parent == FOLDER_UUID
    assert len(journal.pages) == 1
    page = journal.pages[0]
    assert page.rm_path is not None and page.rm_path.name == "c48a9c11-c2ba-4027-a867-0fc7bcb5476a.rm"
    assert page.template == TEMPLATE_RULED  # "P Lines medium"

    from remarksync.rmdata import decode_page, page_text

    assert "coffee" in page_text(decode_page(page.rm_path))


def test_open_rmdoc_roundtrip(backup_root, tmp_path):
    from remarksync.mdcompile import markdown_to_rmdoc

    md = "# Hello\n\n- one\n- two\n\nsome *emphasis* and a [link](https://example.com)\n"
    rmdoc_path = markdown_to_rmdoc(md, "Compiled Doc", tmp_path / "compiled.rmdoc")
    doc = open_rmdoc(rmdoc_path)
    assert doc.name == "Compiled Doc"
    assert doc.file_type == "notebook"
    assert len(doc.pages) >= 1
    assert all(p.rm_path is not None for p in doc.pages)


def test_open_rmdoc_refuses_members_that_escape_the_extraction_directory(tmp_path):
    """A .rmdoc is just a zip, and a zip can name ``../`` in its entries. pull.py
    already refuses tar members that would land outside the directory it is
    filling; the same threat arrives through an archive someone was sent, so it
    gets the same answer rather than a different one.
    """
    import zipfile

    from remarksync.backup import open_rmdoc

    archive = tmp_path / "evil.rmdoc"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("doc.metadata", '{"visibleName": "looks ordinary"}')
        zf.writestr("../escaped.txt", "should never be written")

    with pytest.raises(ValueError, match="outside"):
        open_rmdoc(archive, extract_dir=tmp_path / "into")

    assert not (tmp_path / "escaped.txt").exists()


def test_open_rmdoc_accepts_an_ordinary_archive(tmp_path):
    """The guard must not reject archives that are merely nested."""
    import zipfile

    from remarksync.backup import open_rmdoc

    archive = tmp_path / "fine.rmdoc"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("wrapper/doc.metadata", '{"visibleName": "Notes"}')
        zf.writestr("wrapper/doc.content", '{"fileType": "notebook", "pages": ["0"]}')

    doc = open_rmdoc(archive, extract_dir=tmp_path / "into")
    assert doc.name == "Notes"
