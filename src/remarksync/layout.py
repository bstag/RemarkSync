"""Lay a decoded page out on a canvas: the **Page frame**.

The page decoder (:mod:`rmdata`) says *what* is on a page, in page
coordinates. This module decides *where it goes*: how big the canvas is,
where the tablet page (the "paper") sits on it, whether a template or a
base-document underlay is drawn behind the ink, and where the divider lines
go that mark the paper's edge when ink overflows it.

Everything the painter needs is stated explicitly in the returned
:class:`PageFrame`, so the painter draws instructions and computes no
geometry of its own. The frame is also the test surface: every layout
rule is a plain function of a ``PageModel``.

There is one layout policy, verified against the reMarkable desktop app:
content stays exactly where the tablet put it, and the canvas grows around
the paper when ink was written past its edges (the tablet's canvas is
effectively unbounded). Documents with a base PDF (pdf, epub) get that
page fitted inside the paper at its own aspect ratio, centred, the way the
tablet displays it; notebooks get their template instead.

No Qt dependencies here.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .rmdata import PAGE_HEIGHT, PAGE_WIDTH, PageModel

# Breathing room kept below the lowest content on an overflowing canvas.
BOTTOM_MARGIN = 24.0

Rect = tuple[float, float, float, float]  # x, y, w, h
Segment = tuple[tuple[float, float], tuple[float, float]]


@dataclass
class PageFrame:
    """A page placed on a canvas, ready to paint. All rects and points are
    canvas coordinates in tablet pixels."""

    page: PageModel
    canvas_size: tuple[float, float]
    paper: Rect  # where the tablet page sits on the canvas
    template: str | None = None  # template to draw inside `paper`, None for none
    underlay: Rect | None = None  # where the base-document page is drawn, None for none
    dividers: list[Segment] = field(default_factory=list)

    @property
    def origin(self) -> tuple[float, float]:
        """Top-left of the paper on the canvas (non-zero after overflow)."""
        return self.paper[0], self.paper[1]

    @property
    def width(self) -> float:
        return self.canvas_size[0]

    @property
    def height(self) -> float:
        return self.canvas_size[1]


def lay_out(
    page: PageModel,
    *,
    template: str | None = None,
    page_index: int = 0,
    base_size: tuple[float, float] | None = None,
) -> PageFrame:
    """Place ``page`` on a canvas.

    ``template`` is the page's template id, drawn only when there is no
    underlay and only on the first page of an endless canvas. ``base_size``
    is the base-document page size (any unit; only its aspect matters) and
    turns the underlay on. The input page is not modified.
    """
    xs, ys = _extent(page)
    x_min = min([0.0] + xs)
    y_min = min([0.0] + ys)
    x_max = max([float(PAGE_WIDTH)] + xs)
    y_max = max([float(PAGE_HEIGHT)] + [y + BOTTOM_MARGIN for y in ys])
    # move everything so overflow past the top/left fits (avoid -0.0 origins)
    dx = -x_min if x_min < 0 else 0.0
    dy = -y_min if y_min < 0 else 0.0

    placed = _translated(page, dx, dy) if (dx or dy) else page
    paper: Rect = (dx, dy, PAGE_WIDTH, PAGE_HEIGHT)
    frame = PageFrame(page=placed, canvas_size=(x_max + dx, y_max + dy), paper=paper)

    if base_size is not None:
        frame.underlay = fit_rect(base_size, paper)
    elif template and (page_index == 0 or frame.height <= PAGE_HEIGHT):
        # only the first page of an endless canvas shows a template
        frame.template = template

    frame.dividers = _dividers(paper, frame.canvas_size)
    return frame


# ------------------------------------------------------------------- helpers


def fit_rect(size: tuple[float, float], into: Rect) -> Rect:
    """Largest rect of ``size``'s aspect ratio that fits inside ``into``, centred."""
    w, h = size
    ix, iy, iw, ih = into
    if w <= 0 or h <= 0:
        return into
    scale = min(iw / w, ih / h)
    fw, fh = w * scale, h * scale
    return (ix + (iw - fw) / 2, iy + (ih - fh) / 2, fw, fh)


def _dividers(paper: Rect, canvas: tuple[float, float]) -> list[Segment]:
    """Dashed lines marking each paper edge that content overflows."""
    px, py, pw, ph = paper
    cw, ch = canvas
    out: list[Segment] = []
    if py > 0:
        out.append(((px, py), (px + pw, py)))
    if py + ph < ch:
        out.append(((px, py + ph), (px + pw, py + ph)))
    if px > 0:
        out.append(((px, py), (px, py + ph)))
    if px + pw < cw:
        out.append(((px + pw, py), (px + pw, py + ph)))
    return out


def _extent(page: PageModel) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for s in page.strokes:
        xs += [x for x, _ in s.points]
        ys += [y for _, y in s.points]
    for t in page.texts:
        xs += [t.pos_x, t.pos_x + t.width]
        ys += [t.pos_y, t.pos_y + t.height]
    for hl in page.highlights:
        xs += [x for x, _, _, _ in hl.rectangles] + [x + w for x, _, w, _ in hl.rectangles]
        ys += [y for _, y, _, _ in hl.rectangles] + [y + h for _, y, _, h in hl.rectangles]
    return xs, ys


def _translated(page: PageModel, dx: float, dy: float) -> PageModel:
    """A copy of ``page`` with every position shifted by (dx, dy)."""
    return replace(
        page,
        strokes=[replace(s, points=[(x + dx, y + dy) for x, y in s.points]) for s in page.strokes],
        texts=[replace(t, pos_x=t.pos_x + dx, pos_y=t.pos_y + dy, paragraphs=list(t.paragraphs)) for t in page.texts],
        highlights=[
            replace(h, rectangles=[(x + dx, y + dy, w, hh) for x, y, w, hh in h.rectangles]) for h in page.highlights
        ],
    )
