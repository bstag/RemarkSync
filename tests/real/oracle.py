"""Compare RemarkSync's own painting against the reMarkable desktop app's.

The desktop app keeps its own render of every page it has synced, at
``<store>/<doc-uuid>.thumbnails/<page-uuid>.png``. Those are 384x512 — the
tablet's 1404x1872 paper divided by 3.656, same 3:4 — so they are the
device software's own answer to "where does this content sit on the paper".

That makes them an independent oracle. Strong enough to settle placement,
scale, centring and whether a pen is translucent; too coarse for the
sub-pixel claims (one thumbnail pixel is 3.66 tablet pixels), which need
the tablet's own PDF export instead.

Everything here only reads. Nothing writes to the backup folder or the
desktop app's store.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from remarksync.backup import DocItem, PageRef, iter_docs, scan_backup
from remarksync.layout import PageFrame, lay_out
from remarksync.rmdata import PageModel, decode_page
from remarksync.viewer import PageCanvas, PdfUnderlay

# A pixel at or below this grey counts as ink. Templates are painted at
# 0xB3 by both renderers, so this keeps the pattern out of the ink mask.
INK_LEVEL = 128
# Anything below this counts as a mark of any kind, template included.
MARK_LEVEL = 230


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


@dataclass
class Pairing:
    """One page that both renderers have an opinion about."""

    doc: DocItem
    page: PageRef
    thumbnail: Path

    @property
    def label(self) -> str:
        return f"{self.doc.name} p{self.page.index + 1}"


def pair_pages(raw_root: Path, store: Path, *, notebooks_only: bool = True) -> list[Pairing]:
    """Every page of ``raw_root`` the desktop app has also rendered.

    ``notebooks_only`` keeps documents whose paper is the tablet screen. An
    annotated PDF's paper is the base page, which is not the 3:4 the
    thumbnail frame uses, so those pages are not comparable this way.
    """
    pairs: list[Pairing] = []
    for doc in iter_docs(scan_backup(raw_root)):
        thumbs = store / f"{doc.uuid}.thumbnails"
        if not thumbs.is_dir():
            continue
        if notebooks_only and doc.resolve_base_pdf() is not None:
            continue
        for page in doc.pages:
            if page.rm_path is None:
                continue
            candidate = thumbs / f"{page.rm_path.stem}.png"
            if candidate.exists():
                pairs.append(Pairing(doc, page, candidate))
    return pairs


def build_frame(pair: Pairing, *, force_opaque_above: float | None = None) -> PageFrame:
    """Decode and lay out the page, optionally overriding translucency.

    ``force_opaque_above`` draws every stroke wider than that as opaque ink.
    It exists to A/B a proposed decoder change against the oracle *before*
    the change is made.
    """
    model = decode_page(pair.page.rm_path) if pair.page.rm_path else PageModel()
    if force_opaque_above is not None:
        for stroke in model.strokes:
            if stroke.width > force_opaque_above:
                stroke.highlighter = False
                stroke.alpha = 1.0
    base_size = None
    base_pdf = pair.doc.resolve_base_pdf()
    if base_pdf is not None:
        underlay = PdfUnderlay(base_pdf)
        try:
            base_size = underlay.page_size(pair.page.index)
        finally:
            underlay.close()
    return lay_out(model, template=pair.page.template, page_index=pair.page.index, base_size=base_size)


def render_paper(pair: Pairing, size: tuple[int, int], *, force_opaque_above: float | None = None,
                 frame: PageFrame | None = None) -> Image.Image:
    """Paint the page the way the app does, then crop to the paper and scale
    to ``size`` so it lines up with the desktop app's own render.

    The crop matters: an overflowing page gets a canvas larger than the
    paper, and the thumbnail only ever shows the paper.
    """
    _app()
    underlay = None
    base_size = None
    base_pdf = pair.doc.resolve_base_pdf()
    if base_pdf is not None:
        underlay = PdfUnderlay(base_pdf)
        base_size = underlay.page_size(pair.page.index)

    if frame is None:
        model = decode_page(pair.page.rm_path) if pair.page.rm_path else PageModel()
        if force_opaque_above is not None:
            for stroke in model.strokes:
                if stroke.width > force_opaque_above:
                    stroke.highlighter = False
                    stroke.alpha = 1.0
        frame = lay_out(model, template=pair.page.template, page_index=pair.page.index, base_size=base_size)

    canvas = PageCanvas(pair.doc, pair.page, underlay, scale=1.0, frame=frame)
    try:
        image = canvas.grab().toImage().convertToFormat(QImage.Format_Grayscale8)
        x, y, w, h = frame.paper
        cropped = image.copy(QRect(round(x), round(y), max(1, round(w)), max(1, round(h))))
        return _to_pil(cropped).resize(size, Image.LANCZOS)
    finally:
        canvas.deleteLater()
        if underlay is not None:
            underlay.close()


def paper_box(frame: PageFrame, points: list[tuple[float, float]], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Bounding box of ``points`` (canvas coordinates) inside a render that
    was cropped to the paper and scaled to ``size``."""
    px, py, pw, ph = frame.paper
    sx, sy = size[0] / pw, size[1] / ph
    xs = [(x - px) * sx for x, _ in points]
    ys = [(y - py) * sy for _, y in points]
    return (max(0, int(min(xs))), max(0, int(min(ys))),
            min(size[0], int(max(xs)) + 1), min(size[1], int(max(ys)) + 1))


def _to_pil(image: QImage) -> Image.Image:
    """Grayscale QImage -> PIL, dropping Qt's row padding."""
    image = image.convertToFormat(QImage.Format_Grayscale8)
    width, height = image.width(), image.height()
    buffer = np.frombuffer(image.constBits(), dtype=np.uint8, count=image.sizeInBytes())
    rows = buffer.reshape(height, image.bytesPerLine())[:, :width]
    return Image.fromarray(rows.copy(), mode="L")


def load_thumbnail(path: Path) -> Image.Image:
    return Image.open(path).convert("L")


@dataclass
class Metrics:
    """How alike two renders of the same page are."""

    ink_ours: float  # fraction of pixels that are ink
    ink_theirs: float
    iou: float  # overlap of the two ink masks
    centroid_delta_px: float  # distance between ink centroids, in tablet pixels
    mean_ink_ours: float  # mean grey inside our ink; lower is darker
    mean_ink_theirs: float

    @property
    def ink_ratio(self) -> float:
        return self.ink_ours / self.ink_theirs if self.ink_theirs else float("inf")


def compare(ours: Image.Image, theirs: Image.Image) -> Metrics:
    a = np.asarray(ours, dtype=np.uint8)
    b = np.asarray(theirs, dtype=np.uint8)
    mask_a, mask_b = a <= INK_LEVEL, b <= INK_LEVEL
    union = np.logical_or(mask_a, mask_b).sum()
    # one thumbnail pixel is this many tablet pixels
    tablet_px = 1404 / theirs.width
    return Metrics(
        ink_ours=float(mask_a.mean()),
        ink_theirs=float(mask_b.mean()),
        iou=float(np.logical_and(mask_a, mask_b).sum() / union) if union else 1.0,
        centroid_delta_px=_centroid_delta(mask_a, mask_b) * tablet_px,
        mean_ink_ours=float(a[mask_a].mean()) if mask_a.any() else 255.0,
        mean_ink_theirs=float(b[mask_b].mean()) if mask_b.any() else 255.0,
    )


def _centroid_delta(mask_a: np.ndarray, mask_b: np.ndarray) -> float:
    if not mask_a.any() or not mask_b.any():
        return 0.0 if mask_a.any() == mask_b.any() else float("inf")
    ay, ax = np.argwhere(mask_a).mean(axis=0)
    by, bx = np.argwhere(mask_b).mean(axis=0)
    return float(np.hypot(ax - bx, ay - by))


def contact_sheet(panels: list[tuple[str, Image.Image]], path: Path, *, gap: int = 12) -> Path:
    """Save labelled panels side by side, for looking at with human eyes."""
    from PIL import ImageDraw

    band = 18
    width = sum(p.width for _, p in panels) + gap * (len(panels) + 1)
    height = max(p.height for _, p in panels) + band + gap * 2
    sheet = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(sheet)
    x = gap
    for caption, panel in panels:
        sheet.paste(panel, (x, gap + band))
        draw.text((x, gap + 4), caption, fill=0)
        draw.rectangle([x, gap + band, x + panel.width - 1, gap + band + panel.height - 1], outline=160)
        x += panel.width + gap
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    return path
