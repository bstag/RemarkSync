"""Preserve visible tablet notes in a separate, searchable original-PDF copy.

No Qt. Call synchronously from a CLI or in a separate GUI process: MuPDF is
not safe to use concurrently from threads. All source bytes are captured and
checked again before publication; a failed export never replaces the output.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pymupdf as fitz

from . import __version__
from .backup import DocItem, PageRef, _find_page_rm
from .backupstore import TRANSACTION_DIR
from .inkgeometry import stroke_polygons
from .layout import GROWTH_MARGIN, PDF_SCALE, base_page_rect
from .notelayout import body_columns, fit_notes_to_columns, pdf_font
from .rmdata import PAGE_HEIGHT, PAGE_WIDTH, PageModel, V5_HEADER, decode_page
from .textmetrics import CHECKBOX_GAP, CHECKBOX_SIZE, MARKER_GAP, TEXT_LEFT_PAD, lay_out_lines, style


@dataclass
class ExportReport:
    path: Path
    pages: int
    supplementary_pages: int
    warnings: list[str] = field(default_factory=list)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _root(doc: DocItem) -> Path | None:
    if doc.doc_dir is None:
        return None
    return doc.doc_dir.parent if doc.uuid and doc.doc_dir.name == doc.uuid else doc.doc_dir


def _check_journal(doc: DocItem) -> None:
    root = _root(doc)
    if root and any((root / TRANSACTION_DIR).glob("*.json")):
        raise ValueError("backup publication is incomplete; recover it with a normal pull before exporting")


def _check_missing_scenes(doc: DocItem) -> None:
    if doc.doc_dir is None:
        return
    modern = bool((doc.content.get("cPages") or {}).get("pages"))
    for ref in doc.pages:
        if ref.rm_path is None and _find_page_rm(doc.doc_dir, ref.page_id, ref.index, positional=not modern):
            raise ValueError(f"page {ref.index + 1} gained scene data; refresh the library before exporting")


class _DecodeWarnings(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        if record.name.startswith("rmscene"):
            self.messages.append(record.getMessage())


def _decode(raw):
    messages = _DecodeWarnings()
    logger = logging.getLogger("rmscene")
    logger.addHandler(messages)
    try:
        return decode_page(raw), list(dict.fromkeys(messages.messages))
    finally:
        logger.removeHandler(messages)


def _lines(block):
    return block.lines or lay_out_lines(block.paragraphs or [("PLAIN", block.text)], block.width)


def _font(st):
    return pdf_font(st)


def _text_runs(model):
    """Shared baselines/styles, with searchable substitute fonts and markers."""
    for block in model.texts:
        if not block.layer_visible or not block.text.strip():
            continue
        counters, last_style = {}, None
        for line in _lines(block):
            st = style(line.style)
            x = block.pos_x + TEXT_LEFT_PAD + st.indent
            y = block.pos_y + line.baseline
            if line.first:
                if st.marker in ("number", "letter"):
                    counters[st.name] = counters.get(st.name, 0) + 1 if last_style == st.name else 1
                last_style = st.name
            yield x, y, line.text, st, None
            if not line.first or not st.marker:
                continue
            number = counters.get(st.name, 1)
            label = {"bullet": "\u2022", "bullet2": "\u25e6"}.get(st.marker)
            if st.marker in ("number", "letter"):
                label = f"{number}." if st.marker == "number" else f"{chr(96 + (number - 1) % 26 + 1)}."
                marker_x = x - MARKER_GAP + 12 - _font(st).text_length(label, fontsize=st.size)
            else:
                marker_x = x - MARKER_GAP
            yield marker_x, y, label or "", st, st.marker


def _bounds(model: PageModel, paper: fitz.Rect) -> fitz.Rect:
    """Include pen widths, text glyphs and above-paper notes too."""
    bounds = fitz.Rect(paper)
    for stroke in model.strokes:
        if not stroke.layer_visible:
            continue
        for (x, y), width in zip(stroke.points, stroke.widths):
            r = max(0.2, width / 2)
            bounds |= fitz.Rect(x - r, y - r, x + r, y + r)
    for hl in model.highlights:
        if hl.layer_visible:
            for x, y, w, h in hl.rectangles:
                if w > 0 and h > 0:
                    bounds |= fitz.Rect(x, y, x + w, y + h)
    for x, y, text, st, marker in _text_runs(model):
        font = _font(st)
        if marker in ("box", "box_checked"):
            x -= CHECKBOX_GAP - MARKER_GAP
        width = max(CHECKBOX_SIZE if marker else 1, font.text_length(text, fontsize=st.size))
        # TextWriter may choose a wider fallback for Unicode. The declared
        # text block bounds are included below as well.
        bounds |= fitz.Rect(x, y - st.size * font.ascender, x + width, y - st.size * font.descender)
    for block in model.texts:
        if block.layer_visible and block.text.strip():
            bounds |= fitz.Rect(block.pos_x, block.pos_y, block.pos_x + block.width, block.pos_y + block.height)
    if any(not math.isfinite(v) for v in bounds):
        raise ValueError("page has non-finite annotation coordinates")
    for edge in range(4):
        if abs(bounds[edge] - paper[edge]) > 0.5:
            bounds[edge] += -GROWTH_MARGIN if edge < 2 else GROWTH_MARGIN
    return bounds


def _rgb(hex_color):
    return tuple(int(hex_color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _attachment_name(output, preferred):
    name, counter = preferred, 1
    while name in output.embfile_names():
        counter += 1
        name = f"{Path(preferred).stem}-{counter}{Path(preferred).suffix}"
    return name


def _multiply(page):
    """Multiply the isolated highlighter overlay, including fully opaque pens."""
    doc = page.parent
    kind, value = doc.xref_get_key(page.xref, "Resources")
    if kind != "xref":
        raise ValueError("highlighter overlay has no resource dictionary")
    resources = int(value.split()[0])
    doc.xref_set_key(resources, "ExtGState/RemarksyncMultiply", "<< /BM /Multiply >>")
    stream = doc.get_new_xref()
    doc.update_object(stream, "<< >>")
    doc.update_stream(stream, b"q /RemarksyncMultiply gs\n" + page.read_contents() + b"\nQ\n")
    page.set_contents(stream)


def _draw_ink(page, model, origin, highlighters):
    ox, oy = origin
    for stroke in model.strokes:
        if not stroke.layer_visible or stroke.highlighter != highlighters or not stroke.points:
            continue
        shape = page.new_shape()
        for polygon in stroke_polygons(stroke):
            pts = [fitz.Point((x - ox) / PDF_SCALE, (y - oy) / PDF_SCALE) for x, y in polygon]
            shape.draw_polyline(pts + [pts[0]])
        shape.finish(color=None, fill=_rgb(stroke.color), width=0,
                     fill_opacity=stroke.alpha, even_odd=False)
        shape.commit()
    if highlighters and page.get_contents():
        _multiply(page)


def _draw_text(page, model, origin):
    ox, oy = origin
    writer = fitz.TextWriter(page.rect)
    for x, y, text, st, marker in _text_runs(model):
        pos = fitz.Point((x - ox) / PDF_SCALE, (y - oy) / PDF_SCALE)
        if text:
            writer.append(pos, text, font=_font(st), fontsize=st.size / PDF_SCALE)
        if marker in ("box", "box_checked"):
            left = x - CHECKBOX_GAP + MARKER_GAP
            rect = fitz.Rect((left - ox) / PDF_SCALE, (y - CHECKBOX_SIZE - oy) / PDF_SCALE,
                             (left + CHECKBOX_SIZE - ox) / PDF_SCALE, (y - oy) / PDF_SCALE)
            page.draw_rect(rect, color=(0.1, 0.1, 0.1), width=2 / PDF_SCALE)
            if marker == "box_checked":
                page.draw_polyline([rect.tl + (5 / PDF_SCALE, 13 / PDF_SCALE),
                                    rect.bl + (11 / PDF_SCALE, -5 / PDF_SCALE),
                                    rect.tr + (-4 / PDF_SCALE, 5 / PDF_SCALE)], width=2 / PDF_SCALE)
    if not writer.text_rect.is_empty:
        writer.write_text(page, color=(0.1, 0.1, 0.1))


def _overlay(target, model, bounds):
    """Draw in displayed page coordinates, then derotate into the original."""
    with fitz.open() as overlay:
        for highlighters in (True, False):
            page = overlay.new_page(width=bounds.width / PDF_SCALE, height=bounds.height / PDF_SCALE)
            _draw_ink(page, model, bounds.tl, highlighters)
            if not highlighters:
                _draw_text(page, model, bounds.tl)
        # MuPDF's graft map is sized at the first insertion. Finish every
        # source resource before importing either overlay page.
        for page in overlay:
            if page.get_contents():
                # show_pdf_page's target conversion includes cropbox offsets.
                # Normalize rotation while inserting, then restore it: using
                # a rotated target matrix directly shifts cropped pages.
                rotation = target.rotation
                target.set_rotation(0)
                try:
                    target.show_pdf_page(target.rect, overlay, page.number, rotate=rotation, overlay=True)
                finally:
                    target.set_rotation(rotation)


def _highlights(target, model, bounds):
    for hl in model.highlights:
        if not hl.layer_visible:
            continue
        quads = []
        for x, y, w, h in hl.rectangles:
            if w <= 0 or h <= 0:
                continue
            rect = fitz.Rect(x, y, x + w, y + h) & bounds
            if rect.is_empty:
                continue
            pts = [fitz.Point((p.x - bounds.x0) / PDF_SCALE, (p.y - bounds.y0) / PDF_SCALE)
                   * target.derotation_matrix for p in (rect.tl, rect.tr, rect.bl, rect.br)]
            quads.append(fitz.Quad(pts))
        if quads:
            annot = target.add_highlight_annot(quads)
            annot.set_colors(stroke=_rgb(hl.color))
            annot.set_opacity(hl.alpha)
            annot.set_info(content=hl.text, title="reMarkable highlight")
            annot.update()


def export_pdf(doc: DocItem, destination: Path, *, overwrite: bool = False, progress=None,
               text_layout: str = "tablet") -> ExportReport:
    """Export a copy of the original PDF; append inserted/overflow note sheets.

    Only visible layers are painted. Raw scenes and the untouched original
    PDF are embedded for preservation. Unreadable scenes, missing bases,
    invalid mappings, source changes and unsafe output paths fail explicitly.
    """
    if doc.kind != "doc" or doc.file_type not in ("pdf", "epub"):
        raise ValueError("select an annotated PDF or EPUB with a base PDF")
    if text_layout not in ("tablet", "pdf-column"):
        raise ValueError("text_layout must be tablet or pdf-column")
    source = doc.resolve_base_pdf()
    if source is None:
        raise ValueError("the original/base PDF is missing")
    destination = Path(destination).resolve()
    root = _root(doc)
    if destination == source.resolve() or (root and destination.is_relative_to(root.resolve())):
        raise ValueError("save the preservation PDF outside the raw backup; source files must remain unchanged")
    if destination.suffix.lower() != ".pdf":
        raise ValueError("the destination must have a .pdf extension")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {destination}; use --overwrite to replace an export")
    _check_journal(doc)
    _check_missing_scenes(doc)
    captured = {}

    def capture(path):
        path = Path(path)
        if path not in captured:
            captured[path] = path.read_bytes()
        return captured[path]

    original = capture(source)
    if doc.base_path and doc.base_path != source:
        capture(doc.base_path)
    if root:
        prefix = doc.uuid if root != doc.doc_dir else ""
        for suffix in ("content", "metadata", "pagedata"):
            path = root / f"{prefix}.{suffix}"
            if not path.exists() and not prefix:
                path = root / suffix
            if path.exists():
                data = capture(path)
                if suffix == "content" and doc.content and json.loads(data) != doc.content:
                    raise ValueError("document mapping changed; refresh the library before exporting")
    warnings = []
    entries = []
    raw_scenes = []
    models = []
    for ref in doc.pages:
        raw = capture(ref.rm_path) if ref.rm_path is not None else None
        model, diagnostics = _decode(raw) if raw is not None else (PageModel(), [])
        if diagnostics:
            warnings.append(f"Tablet page {ref.index + 1} has decoder warnings; inspect its diagnostics in the manifest and compare with the tablet export.")
        if model.error:
            raise ValueError(f"page {ref.index + 1}: {model.error}; export was not published")
        if raw is not None:
            raw_scenes.append((f"scene-{ref.index + 1}.rm", raw))
        if raw and raw.startswith(V5_HEADER):
            warnings.append("Older v5 ink has a known rendering discrepancy; compare with the tablet export.")
        if any(t.layer_visible and t.text for t in model.texts):
            warnings.append("Typed notes use substitute PDF fonts; glyph shapes may differ from the tablet.")
        if any(s.layer_visible and ("PENCIL" in s.tool or "PAINTBRUSH" in s.tool) for s in model.strokes):
            warnings.append("Pencil/paintbrush texture is approximated by vector ink and average opacity.")
        models.append((model, diagnostics))
    supplementary = 0
    with fitz.open(stream=original, filetype="pdf") as base, fitz.open(stream=original, filetype="pdf") as output:
        if base.needs_pass:
            raise ValueError("password-protected PDFs are not supported")
        if not base.page_count:
            raise ValueError("the original PDF has no pages")
        if base.get_sigflags() > 0:
            warnings.append("Adding notes invalidates PDF signatures on the derivative; the untouched original is attached.")
        warnings.append("Placement uses the measured fixed-scale tablet mapping; unusual device zoom/pan settings need comparison with a tablet export.")
        used = set()
        column_cache = {}
        for ref, (model, diagnostics) in zip(doc.pages, models):
            base_index = ref.base_index
            if base_index is not None and not 0 <= base_index < base.page_count:
                raise ValueError(f"page {ref.index + 1} maps to missing original PDF page {base_index + 1}")
            paper_width, paper_height = model.paper_size or (PAGE_WIDTH, PAGE_HEIGHT)
            if base_index is None:
                paper = fitz.Rect(0, 0, paper_width, paper_height)
            else:
                size = base[base_index].rect
                x, y, w, h = base_page_rect((size.width, size.height), paper_width)
                paper = fitz.Rect(x, y, x + w, y + h)
            raw_model = model
            note_layout = []
            if text_layout == "pdf-column" and any(t.layer_visible and t.text.strip() for t in model.texts):
                # Inserted pages borrow the nearest original page's column.
                source_index = base_index
                if source_index is None:
                    nearest = min((p for p in doc.pages if p.base_index is not None),
                                  key=lambda p: abs(p.index - ref.index), default=None)
                    source_index = nearest.base_index if nearest else 0
                if not 0 <= source_index < base.page_count:
                    raise ValueError("the text-column source maps to a missing PDF page")
                if source_index not in column_cache:
                    column_cache[source_index] = body_columns(base[source_index])
                columns, warning = column_cache[source_index]
                if warning:
                    warnings.append(warning)
                    columns = [fitz.Rect(paper.width / PDF_SCALE * 0.1, 0,
                                         paper.width / PDF_SCALE * 0.9, paper.height / PDF_SCALE)]
                if base_index is None and not warning:
                    # Use the original column's width on the inserted sheet,
                    # centered if its paper is narrower than the original.
                    available = paper.width / PDF_SCALE
                    columns = [fitz.Rect(max(0, min(c.x0, available - min(c.width, available * 0.9))), 0,
                                         max(0, min(c.x0, available - min(c.width, available * 0.9)))
                                         + min(c.width, available * 0.9), paper.height / PDF_SCALE) for c in columns]
                model, note_layout = fit_notes_to_columns(model, paper, columns)
                warnings.append("Typed notes are reflowed to PDF text columns; handwriting and highlights retain tablet positions.")
            bounds = _bounds(model, paper)
            entry = {"tablet_page": ref.index + 1, "page_id": ref.page_id,
                     "original_page": None if base_index is None else base_index + 1,
                     "outputs": [], "template": ref.template,
                     "page_metadata": ref.metadata,
                     "decoder_diagnostics": diagnostics,
                     "typed_notes": [asdict(t) for t in raw_model.texts],
                     "note_layout": note_layout,
                     "highlights": [asdict(h) for h in model.highlights]}
            if base_index is not None:
                if base_index in used:
                    output.insert_pdf(base, from_page=base_index, to_page=base_index)
                    index = output.page_count - 1
                    supplementary += 1
                else:
                    index = base_index
                used.add(base_index)
                target = output[index]
                _overlay(target, model, paper)
                _highlights(target, model, paper)
                entry["outputs"].append({"page": index + 1, "kind": "annotated_original"})
            if base_index is None or any(abs(bounds[i] - paper[i]) > 0.5 for i in range(4)):
                if max(bounds.width, bounds.height) / PDF_SCALE > 14400:
                    raise ValueError(f"page {ref.index + 1} is too large for a single PDF note sheet")
                target = output.new_page(width=bounds.width / PDF_SCALE, height=bounds.height / PDF_SCALE)
                if base_index is not None:
                    rect = fitz.Rect((paper.x0 - bounds.x0) / PDF_SCALE, (paper.y0 - bounds.y0) / PDF_SCALE,
                                     (paper.x1 - bounds.x0) / PDF_SCALE, (paper.y1 - bounds.y0) / PDF_SCALE)
                    if base[base_index].get_contents():
                        target.show_pdf_page(rect, base, base_index)
                elif ref.template != "blank":
                    warnings.append("Inserted note-sheet templates are recorded in the manifest but not drawn.")
                _overlay(target, model, bounds)
                _highlights(target, model, bounds)
                supplementary += 1
                entry["outputs"].append({"page": target.number + 1,
                                         "kind": "inserted_note" if base_index is None else "overflow_context",
                                         "tablet_bounds": list(bounds)})
            entries.append(entry)
            if progress:
                progress(ref.index + 1, len(doc.pages))
        warnings = list(dict.fromkeys(warnings))
        original_attachment = _attachment_name(output, "original.pdf")
        output.embfile_add(original_attachment, original, filename="original.pdf", desc="Untouched original/base PDF")
        if doc.base_path and doc.base_path != source:
            name = _attachment_name(output, f"original{doc.base_path.suffix}")
            output.embfile_add(name, captured[doc.base_path], filename=f"original{doc.base_path.suffix}", desc="Untouched base document")
        for name, raw in raw_scenes:
            attachment = _attachment_name(output, name)
            output.embfile_add(attachment, raw, filename=attachment, desc="Original tablet scene, including hidden layers")
            entry = next(e for e in entries if name == f"scene-{e['tablet_page']}.rm")
            entry["scene_attachment"] = attachment
        manifest = {"format": "remarksync-preservation-v1", "version": __version__,
                    "created_utc": datetime.now(timezone.utc).isoformat(), "document_uuid": doc.uuid,
                    "document_name": doc.name, "last_modified": doc.last_modified,
                    "original_sha256": _hash(original), "original_page_count": base.page_count,
                    "original_attachment": original_attachment,
                    "policy": "original order retained; visible layers painted; extra note sheets appended",
                    "text_layout": text_layout,
                    "warnings": warnings, "pages": entries, "document_content": doc.content,
                    "sources": [{"name": p.name, "sha256": _hash(data)} for p, data in captured.items()]}
        output.embfile_add(_attachment_name(output, "remarksync-manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                           filename="remarksync-manifest.json", desc="Page mapping, provenance and fidelity warnings")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".remarksync-export-", suffix=".pdf", dir=destination.parent)
        os.close(descriptor)
        try:
            output.save(temporary, garbage=3, deflate=True)
            with fitz.open(temporary) as check:
                if check.page_count != output.page_count or check.embfile_get(original_attachment) != original:
                    raise ValueError("saved export failed preservation verification")
            _check_journal(doc)
            _check_missing_scenes(doc)
            for path, data in captured.items():
                if _hash(path.read_bytes()) != _hash(data):
                    raise ValueError(f"source changed during export: {path.name}; refresh and retry")
            with open(temporary, "r+b") as handle:
                os.fsync(handle.fileno())
            if overwrite:
                os.replace(temporary, destination)
            else:
                # Hard-link publication is atomic and refuses a destination
                # created by another exporter since the initial check.
                os.link(temporary, destination)
            return ExportReport(destination, output.page_count, supplementary, warnings)
        finally:
            Path(temporary).unlink(missing_ok=True)


def document_payload(doc: DocItem) -> dict:
    """Serializable input for a separate export process."""
    fields = ("kind", "uuid", "name", "file_type", "base_path", "base_pdf", "doc_dir", "last_modified", "content")
    payload = {key: str(value) if isinstance(value, Path) else value
               for key in fields if (value := getattr(doc, key)) is not None}
    payload["pages"] = [dict(asdict(ref), rm_path=str(ref.rm_path) if ref.rm_path else None) for ref in doc.pages]
    return payload


def document_from_payload(payload: dict) -> DocItem:
    payload = dict(payload)
    refs = payload.pop("pages")
    for key in ("base_path", "base_pdf", "doc_dir"):
        if payload.get(key):
            payload[key] = Path(payload[key])
    return DocItem(**payload, pages=[PageRef(**dict(ref, rm_path=Path(ref["rm_path"]) if ref["rm_path"] else None))
                                     for ref in refs])
