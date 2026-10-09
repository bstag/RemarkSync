"""Preservation checks: searchable originals, visible notes, mapping and failure safety."""

import hashlib
import json
from pathlib import Path

import fitz
import pytest

from remarksync.backup import DocItem, PageRef, iter_docs, scan_backup
from remarksync.layout import PDF_SCALE, base_page_rect
from remarksync.pdfexport import export_pdf
from remarksync.rmdata import Highlight, PageModel, Stroke, TextBlock

from .conftest import PDF_UUID, page_rm, page_rm_v5, write_content


def _doc(tmp_path, rotation=0, crop=False):
    raw = tmp_path / "raw"
    raw.mkdir()
    path = raw / "original.pdf"
    with fitz.open() as base:
        p = base.new_page(width=400, height=500)
        p.insert_text((80, 100), "Preserve original searchable text")
        p.draw_rect(fitz.Rect(90, 140, 210, 200), color=(0, 0, 0))
        if crop:
            p.set_cropbox(fitz.Rect(40, 50, 360, 450))
        p.set_rotation(rotation)
        base.new_page(width=400, height=500).insert_text((80, 100), "Second original page")
        base.save(path)
    scene = raw / "scene.rm"
    scene.write_bytes(page_rm(text="Real decoded note"))
    return DocItem(uuid="paper", name="Preservation", file_type="pdf", base_pdf=path,
                   doc_dir=raw, pages=[PageRef(0, scene, page_id="scene", base_index=0)])


def _manifest(pdf):
    return json.loads(pdf.embfile_get("remarksync-manifest.json"))


def test_real_decoder_preserves_original_and_searchable_notes(backup_root, tmp_path_factory):
    doc = next(d for d in iter_docs(scan_backup(backup_root)) if d.uuid == PDF_UUID)
    original = doc.base_pdf.read_bytes()
    report = export_pdf(doc, tmp_path_factory.mktemp("exports") / "export.pdf")
    with fitz.open(report.path) as result:
        assert "searchable words" in result[0].get_text()
        assert "base pdf page 1" in result[1].get_text()
        assert result.embfile_get("original.pdf") == original
        assert result.embfile_get("scene-1.rm") == doc.pages[0].rm_path.read_bytes()
        assert _manifest(result)["original_sha256"] == hashlib.sha256(original).hexdigest()
    assert doc.base_pdf.read_bytes() == original


def test_confirmed_yellow_glyph_placeholder_renders_yellow(tmp_path):
    from rmscene import scene_items as si

    doc = _doc(tmp_path)
    left = base_page_rect((400, 500))[0]
    glyph = si.GlyphRange(start=0, length=4, text="note", color=si.PenColor.YELLOW,
                         rectangles=[si.Rectangle(left + 100 * PDF_SCALE - 702,
                                                  150 * PDF_SCALE, 80 * PDF_SCALE, 20 * PDF_SCALE)],
                         color_rgba=(0, 0, 0, 255))
    doc.pages[0].rm_path.write_bytes(page_rm(glyphs=[glyph]))
    original = doc.base_pdf.read_bytes()
    report = export_pdf(doc, tmp_path / "yellow.pdf")
    with fitz.open(report.path) as pdf:
        red, green, blue = pdf[0].get_pixmap(alpha=False).pixel(120, 160)
        assert red > blue + 80 and green > blue + 40
        assert _manifest(pdf)["pages"][0]["highlights"][0]["color"] == "#f2b800"
        assert pdf.embfile_get("original.pdf") == original


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("crop", [False, True])
def test_highlight_and_ink_alignment_on_rotated_cropped_original(tmp_path, monkeypatch, rotation, crop):
    doc = _doc(tmp_path, rotation, crop)
    with fitz.open(doc.base_pdf) as original:
        size = original[0].rect
        left = base_page_rect((size.width, size.height))[0]
    # Known displayed coordinates in PDF points. All transforms, including
    # the original's crop and rotation, must put the visible note here.
    model = PageModel(
        highlights=[Highlight("marked words", [(left + 20 * PDF_SCALE, 30 * PDF_SCALE,
                                                80 * PDF_SCALE, 15 * PDF_SCALE)], color="#00ff00", alpha=0.5)],
        strokes=[Stroke([(left + 30 * PDF_SCALE, 75 * PDF_SCALE),
                         (left + 110 * PDF_SCALE, 75 * PDF_SCALE)], "#0000ff", 5 * PDF_SCALE)],
        texts=[TextBlock("Searchable note", left + 25 * PDF_SCALE, 220 * PDF_SCALE, 250 * PDF_SCALE,
                         paragraphs=[("PLAIN", "Searchable note")])])
    monkeypatch.setattr("remarksync.pdfexport.decode_page", lambda raw: model)
    report = export_pdf(doc, tmp_path / "annotated.pdf")
    with fitz.open(report.path) as pdf:
        page = pdf[0]
        assert page.rotation == rotation
        assert "Searchable note" in page.get_text()
        annotation = next(page.annots())
        assert annotation.info["content"] == "marked words"
        pix = page.get_pixmap(alpha=False)
        assert pix.pixel(60, 37)[1] > pix.pixel(60, 37)[0]  # green highlight
        assert pix.pixel(60, 75)[2] > 200 and pix.pixel(60, 75)[0] < 40  # blue ink
        # Original visible dimensions are kept, including rotated crop boxes.
        assert (page.rect.width, page.rect.height) == (size.width, size.height)


def test_inserted_duplicate_deleted_and_margin_notes_are_retained(tmp_path, monkeypatch):
    doc = _doc(tmp_path)
    doc.pages = [PageRef(0, doc.pages[0].rm_path, page_id="inserted", base_index=None),
                 PageRef(1, doc.pages[0].rm_path, page_id="second", base_index=1),
                 PageRef(2, doc.pages[0].rm_path, page_id="duplicate", base_index=1)]
    note = PageModel(texts=[TextBlock("Above paper note", -300, -180, 600,
                                     paragraphs=[("PLAIN", "Above paper note")])])
    monkeypatch.setattr("remarksync.pdfexport.decode_page", lambda raw: note)
    report = export_pdf(doc, tmp_path / "extra.pdf")
    with fitz.open(report.path) as pdf:
        assert "Preserve original searchable text" in pdf[0].get_text()  # unreferenced original retained
        manifest = _manifest(pdf)
        assert manifest["pages"][0]["original_page"] is None
        for entry in manifest["pages"]:
            full_sheet = entry["outputs"][-1]["page"] - 1
            assert "Above paper note" in pdf[full_sheet].get_text()
        assert report.supplementary_pages == 4
        assert "Second original page" in pdf[4].get_text()  # duplicate original copy


def test_hidden_layers_are_not_painted_but_raw_scene_is_preserved(tmp_path, monkeypatch):
    doc = _doc(tmp_path)
    model = PageModel(texts=[TextBlock("Hidden note", 100, 100, 600, layer_visible=False)],
                      highlights=[Highlight("hidden", [(0, 0, 100, 100)], layer_visible=False)],
                      strokes=[Stroke([(0, 0), (300, 300)], "#ff0000", 30, layer_visible=False)])
    monkeypatch.setattr("remarksync.pdfexport.decode_page", lambda raw: model)
    report = export_pdf(doc, tmp_path / "visible.pdf")
    with fitz.open(report.path) as pdf:
        assert pdf.page_count == 2
        assert "Hidden note" not in pdf[0].get_text()
        assert list(pdf[0].annots()) == []
        assert _manifest(pdf)["pages"][0]["typed_notes"][0]["text"] == "Hidden note"
        assert pdf.embfile_get("scene-1.rm") == doc.pages[0].rm_path.read_bytes()


def test_unreadable_page_or_invalid_mapping_does_not_replace_export(tmp_path):
    doc = _doc(tmp_path)
    out = tmp_path / "existing.pdf"
    out.write_bytes(b"previous preservation")
    doc.pages[0].rm_path.write_bytes(b"corrupted scene")
    with pytest.raises(ValueError, match="not a readable"):
        export_pdf(doc, out, overwrite=True)
    assert out.read_bytes() == b"previous preservation"
    doc.pages[0].rm_path.write_bytes(page_rm())
    doc.pages[0].base_index = 50
    with pytest.raises(ValueError, match="missing original"):
        export_pdf(doc, out, overwrite=True)
    assert out.read_bytes() == b"previous preservation"


def test_source_mutation_during_export_is_not_published(tmp_path):
    doc = _doc(tmp_path)
    out = tmp_path / "unsafe.pdf"
    def change(*args):
        doc.pages[0].rm_path.write_bytes(page_rm(text="changed during export"))
    with pytest.raises(ValueError, match="source changed"):
        export_pdf(doc, out, progress=change)
    assert not out.exists()
    assert not list(tmp_path.glob(".remarksync-export-*"))


def test_refuses_source_overwrite_and_existing_output_without_flag(tmp_path):
    doc = _doc(tmp_path)
    original = doc.base_pdf.read_bytes()
    with pytest.raises(ValueError, match="outside the raw backup"):
        export_pdf(doc, doc.base_pdf, overwrite=True)
    assert doc.base_pdf.read_bytes() == original
    out = tmp_path / "exists.pdf"
    out.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        export_pdf(doc, out)
    assert out.read_bytes() == b"keep"


def test_cli_exports_selected_document_and_reports_errors(backup_root, tmp_path_factory, capsys):
    from remarksync.__main__ import main
    out = tmp_path_factory.mktemp("exports") / "cli.pdf"
    assert main(["export", str(backup_root), str(out), "--document", PDF_UUID]) == 0
    assert "wrote" in capsys.readouterr().out
    assert main(["export", str(backup_root), str(out), "--document", PDF_UUID]) == 1
    assert "already exists" in capsys.readouterr().err


def test_stale_page_mapping_rejected_before_export(backup_root, tmp_path_factory):
    doc = next(d for d in iter_docs(scan_backup(backup_root)) if d.uuid == PDF_UUID)
    write_content(backup_root, PDF_UUID, {"fileType": "pdf", "pages": ["1", "0"]})
    with pytest.raises(ValueError, match="mapping changed"):
        export_pdf(doc, tmp_path_factory.mktemp("exports") / "stale.pdf")


@pytest.mark.parametrize("alpha", [0.4, 1.0])
def test_highlighter_is_one_vector_fill_with_multiply(tmp_path, monkeypatch, alpha):
    doc = _doc(tmp_path)
    left = base_page_rect((400, 500))[0]
    model = PageModel(strokes=[Stroke([(left + 80 * PDF_SCALE, 100 * PDF_SCALE),
                                     (left + 200 * PDF_SCALE, 100 * PDF_SCALE)],
                                     "#ffff00", 30 * PDF_SCALE, highlighter=True, alpha=alpha),
                               Stroke([(left + 50 * PDF_SCALE, 200 * PDF_SCALE),
                                       (left + 250 * PDF_SCALE, 200 * PDF_SCALE)], "#0000ff", 5)])
    monkeypatch.setattr("remarksync.pdfexport.decode_page", lambda raw: model)
    report = export_pdf(doc, tmp_path / "highlighter.pdf")
    with fitz.open(report.path) as pdf:
        # The translucent overlay must multiply rather than whiten the text.
        states = "\n".join(pdf.xref_object(i) for i in range(1, pdf.xref_length()))
        assert "/BM /Multiply" in states
        assert pdf[0].get_pixmap().pixel(180, 100)[2] < 220


def test_decoder_diagnostics_are_recorded_for_preservation(tmp_path, monkeypatch):
    import logging
    doc = _doc(tmp_path)
    def unsupported(raw):
        logging.getLogger("rmscene.tagged_block_reader").warning("Some data has not been read")
        return PageModel()
    monkeypatch.setattr("remarksync.pdfexport.decode_page", unsupported)
    report = export_pdf(doc, tmp_path / "diagnostics.pdf")
    assert any("decoder warnings" in warning for warning in report.warnings)
    with fitz.open(report.path) as pdf:
        assert _manifest(pdf)["pages"][0]["decoder_diagnostics"] == ["Some data has not been read"]


def test_worker_payload_roundtrip_and_no_qt_dependency(tmp_path):
    from remarksync.pdfexport import document_from_payload, document_payload
    doc = _doc(tmp_path)
    restored = document_from_payload(json.loads(json.dumps(document_payload(doc))))
    assert restored.pages == doc.pages
    assert restored.base_pdf == doc.base_pdf
    import ast
    import remarksync.pdfexport as module
    source = Path(module.__file__)
    imports = [n.module for n in ast.walk(ast.parse(source.read_text())) if isinstance(n, ast.ImportFrom)]
    assert not any(name and ("viewer" in name or "PySide" in name) for name in imports)


def test_preserves_existing_pdf_attachments_and_annotations(tmp_path):
    doc = _doc(tmp_path)
    with fitz.open(doc.base_pdf) as original:
        original.embfile_add("original.pdf", b"existing attachment")
        original[0].add_text_annot((40, 40), "Existing PDF comment")
        original.saveIncr()
    source = doc.base_pdf.read_bytes()
    report = export_pdf(doc, tmp_path / "existing-annotations.pdf")
    with fitz.open(report.path) as result:
        assert result.embfile_get("original.pdf") == b"existing attachment"
        manifest = _manifest(result)
        assert result.embfile_get(manifest["original_attachment"]) == source
        assert any(a.info["content"] == "Existing PDF comment" for a in result[0].annots())


def test_unicode_notes_and_legacy_warning(tmp_path, monkeypatch):
    doc = _doc(tmp_path)
    doc.pages[0].rm_path.write_bytes(page_rm_v5([(2, 0, 3, [(100, 100), (300, 200)])]))
    report = export_pdf(doc, tmp_path / "legacy.pdf")
    assert any("v5" in w for w in report.warnings)
    left = base_page_rect((400, 500))[0]
    text = "Note: café — 你好"
    model = PageModel(texts=[TextBlock(text, left + 20 * PDF_SCALE, 180 * PDF_SCALE, 900,
                                     paragraphs=[("PLAIN", text)])])
    monkeypatch.setattr("remarksync.pdfexport.decode_page", lambda raw: model)
    report = export_pdf(doc, tmp_path / "unicode.pdf")
    with fitz.open(report.path) as pdf:
        assert "café" in pdf[0].get_text()
        assert "你好" in pdf[0].get_text().replace(" ", "")
        assert _manifest(pdf)["pages"][0]["typed_notes"][0]["text"] == text


def test_blank_base_page_can_have_margin_note_sheet(tmp_path, monkeypatch):
    doc = _doc(tmp_path)
    with fitz.open() as original:
        original.new_page(width=400, height=500)
        original.save(doc.base_pdf)
    model = PageModel(strokes=[Stroke([(-500, -50), (-400, -50)], "#ff0000", 10)])
    monkeypatch.setattr("remarksync.pdfexport.decode_page", lambda raw: model)
    report = export_pdf(doc, tmp_path / "blank-margin.pdf")
    assert report.pages == 2
    with fitz.open(report.path) as pdf:
        assert pdf[1].get_drawings()


def test_incomplete_publication_blocks_export(tmp_path):
    doc = _doc(tmp_path)
    journals = doc.doc_dir / ".remarksync-transactions"
    journals.mkdir()
    (journals / "uncommitted.json").write_text("{}")
    with pytest.raises(ValueError, match="publication is incomplete"):
        export_pdf(doc, tmp_path / "incomplete.pdf")


def test_new_scene_on_previous_unannotated_page_requires_refresh(backup_root, tmp_path_factory):
    doc = next(d for d in iter_docs(scan_backup(backup_root)) if d.uuid == PDF_UUID)
    # The second original page has no .rm file when scanned.
    assert doc.pages[1].rm_path is None
    scene = doc.doc_dir / "1/1.rm"
    scene.parent.mkdir(parents=True)
    scene.write_bytes(page_rm(text="New note"))
    with pytest.raises(ValueError, match="gained scene data"):
        export_pdf(doc, tmp_path_factory.mktemp("exports") / "stale-scenes.pdf")


def test_cli_exports_single_pdf_archive(backup_root, tmp_path_factory):
    import zipfile
    from remarksync.__main__ import main
    folder = tmp_path_factory.mktemp("archives")
    archive = folder / "paper.rmdoc"
    with zipfile.ZipFile(archive, "w") as zipped:
        for suffix in ("metadata", "content", "pdf", "pagedata"):
            zipped.write(backup_root / f"{PDF_UUID}.{suffix}", f".{suffix}")
        zipped.write(backup_root / PDF_UUID / "0/0.rm", "0/0.rm")
    out = folder / "archive-annotated.pdf"
    assert main(["export", str(archive), str(out)]) == 0
    with fitz.open(out) as pdf:
        assert "base pdf page 0" in pdf[0].get_text()
