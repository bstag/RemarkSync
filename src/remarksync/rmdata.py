"""Parse .rm page files into a pure-Python page model.

The heavy lifting of the binary format is done by the ``rmscene`` library;
this module turns its scene trees into simple dataclasses that the renderer,
search indexer and exporters consume. No Qt dependencies here.
"""

from __future__ import annotations

import io
import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path

from rmscene import read_tree
from rmscene import scene_items as si
from rmscene.text import TextDocument

log = logging.getLogger(__name__)

# reMarkable 2 portrait page size in tablet pixels (Paper Pro pages render on
# the same canvas; PageInfoBlock has no per-page size as of software 3.x).
PAGE_WIDTH = 1404
PAGE_HEIGHT = 1872

# reMarkable accent colours (sRGB approximations of the tablet palette).
PEN_COLORS: dict[str, str] = {
    "BLACK": "#1a1a1a",
    "GRAY": "#808080",
    "WHITE": "#ffffff",
    "YELLOW": "#f2b800",
    "GREEN": "#00a651",
    "PINK": "#e91e63",
    "BLUE": "#0072ce",
    "RED": "#ea2027",
    "GRAY_OVERLAP": "#808080",
    "HIGHLIGHT": "#ffd400",
    "GREEN_2": "#2ecc71",
    "CYAN": "#00bcd4",
    "MAGENTA": "#e040fb",
    "YELLOW_2": "#ffe082",
}

# Base stroke widths in tablet pixels for each pen, before thickness_scale.
PEN_WIDTHS: dict[str, float] = {
    "BALLPOINT_1": 2.2,
    "BALLPOINT_2": 2.2,
    "FINELINER_1": 4.5,
    "FINELINER_2": 4.5,
    "CALLIGRAPHY": 3.5,
    "PENCIL_1": 2.0,
    "PENCIL_2": 2.0,
    "MECHANICAL_PENCIL_1": 2.0,
    "MECHANICAL_PENCIL_2": 2.0,
    "PAINTBRUSH_1": 4.0,
    "PAINTBRUSH_2": 4.0,
    "MARKER_1": 12.0,
    "MARKER_2": 12.0,
    "HIGHLIGHTER_1": 28.0,
    "HIGHLIGHTER_2": 28.0,
    "SHADER": 24.0,
}

HIGHLIGHTER_TOOLS = {"HIGHLIGHTER_1", "HIGHLIGHTER_2", "SHADER", "MARKER_1", "MARKER_2"}
ERASER_TOOLS = {"ERASER", "ERASER_AREA"}

# reMarkable software 2.x wrote "version=5" .lines files; the format was
# extended in-place around software 2.13 (extra per-line records), so we
# parse it with a tolerant scanner rather than a strict grammar.
V5_HEADER = b"reMarkable .lines file, version=5"
V5_COLORS = {0: "BLACK", 1: "GRAY", 2: "WHITE"}


@dataclass
class Stroke:
    """A pen stroke: polyline in tablet page coordinates."""

    points: list[tuple[float, float]]
    color: str  # hex
    width: float  # tablet px, already includes thickness_scale
    highlighter: bool = False
    layer_visible: bool = True


@dataclass
class TextBlock:
    """Typed text box in page coordinates (top-left corner + width)."""

    text: str
    pos_x: float
    pos_y: float
    width: float
    height: float = 70.0
    paragraphs: list[tuple[str, str]] = field(default_factory=list)  # (style, text)


@dataclass
class GlyphHighlight:
    """Text highlighted inside a base PDF page (search gold)."""

    text: str
    rectangles: list[tuple[float, float, float, float]] = field(default_factory=list)


@dataclass
class PageModel:
    strokes: list[Stroke] = field(default_factory=list)
    texts: list[TextBlock] = field(default_factory=list)
    highlights: list[GlyphHighlight] = field(default_factory=list)
    error: str | None = None
    # content size in page units after normalisation (width capped at the
    # page; height grows for endless-canvas journal pages)
    content_size: tuple[float, float] = (PAGE_WIDTH, PAGE_HEIGHT)
    # where the PDF page's top-left corner sits in canvas coordinates —
    # non-zero when ink was written beyond the page sides
    origin: tuple[float, float] = (0.0, 0.0)

    @property
    def has_content(self) -> bool:
        return bool(self.strokes or self.texts or self.highlights)


# v6 text-block layout metrics (from rmc, the maintained reference exporter)
TEXT_TOP_Y = -88
LINE_HEIGHTS = {
    "PLAIN": 70, "BULLET": 35, "BULLET2": 35, "BOLD": 70,
    "HEADING": 150, "CHECKBOX": 35, "CHECKBOX_CHECKED": 35,
}
TOP_MARGIN = 24.0


def _visible(group: si.Group) -> bool:
    try:
        return group.visible.value is not False
    except Exception:
        return True


def _anchor_positions(root_text) -> dict:
    """Vertical position (raw coords) of each text-line anchor, mirroring
    rmc: root text position, then accumulated per-paragraph line heights."""
    pos = {}
    if root_text is None:
        return pos
    try:
        doc = TextDocument.from_scene_item(root_text)
        ypos = root_text.pos_y + TEXT_TOP_Y
        for para in doc.contents:
            pos[para.start_id] = ypos
            for sub in para.contents:
                for char_id in sub.i:
                    pos[char_id] = ypos
            style = getattr(para.style, "value", None)
            ypos += LINE_HEIGHTS.get(getattr(style, "name", "PLAIN"), 70)
    except Exception as exc:
        log.debug("anchor extraction failed: %s", exc)
    return pos


def _group_anchor(group: si.Group, anchor_pos: dict) -> tuple[float, float]:
    """Translation contributed by an anchored layer (ink pinned to a text line)."""
    if group.anchor_id is None:
        return 0.0, 0.0
    ax = group.anchor_origin_x.value if group.anchor_origin_x is not None else 0.0
    ay = anchor_pos.get(group.anchor_id.value, 0.0)
    return float(ax), float(ay)


def parse_rm(source: Path | bytes, pdf_mode: bool = False) -> PageModel:
    """Parse a .rm file (path or raw bytes) into a PageModel.

    Notebook pages are normalised onto the page (content centred, y from
    the top margin, height grows for endless-canvas pages). PDF annotation
    pages (``pdf_mode=True``) keep their true position relative to the
    base PDF: x is centre-relative in v6 and gets the +half-page shift, y
    is already measured from the page top, and no normalisation is applied
    so ink stays on whatever it annotates.
    """
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        data = Path(source).read_bytes()

    if data[:33] == V5_HEADER:
        page = _parse_v5(data)
        if not pdf_mode:
            _normalise(page)
        return page

    try:
        tree = read_tree(io.BytesIO(data))
    except Exception as exc:  # unparseable page: render as blank, stay usable
        log.warning("could not parse %s: %s", source if isinstance(source, Path) else "<bytes>", exc)
        return PageModel(error=str(exc))

    page = PageModel()
    anchor_pos = _anchor_positions(tree.root_text)
    y_shift = 0.0 if pdf_mode else PAGE_HEIGHT / 2

    def visit(item: si.SceneItem, ax: float, ay: float, visible: bool) -> None:
        if isinstance(item, si.Group):
            vis = visible and _visible(item)
            gx, gy = _group_anchor(item, anchor_pos)
            for child in item.children.values():
                visit(child, ax + gx, ay + gy, vis)
        elif isinstance(item, si.Line):
            add_line(item, ax, ay, visible)
        elif isinstance(item, si.GlyphRange):
            rects = [(r.x, r.y, r.w, r.h) for r in item.rectangles]
            if rects:
                # the tablet stores a small sub-line-height rectangle for a
                # leading verse number but does not render it; match that
                # by dropping rects much shorter than the highlight's bands
                band_h = max(h for *_x, _y, _w, h in rects)
                rects = [r for r in rects if r[3] >= 0.75 * band_h]
            page.highlights.append(
                GlyphHighlight(
                    text=item.text or "",
                    rectangles=[
                        (x + PAGE_WIDTH / 2, y + (0.0 if pdf_mode else y_shift), w, h)
                        for x, y, w, h in rects
                    ],
                )
            )
        elif isinstance(item, si.Text):
            page.texts.append(text_block(item, ax, ay))

    def add_line(line: si.Line, ax: float, ay: float, visible: bool) -> None:
        tool = getattr(line.tool, "name", str(line.tool))
        if tool in ERASER_TOOLS:
            return
        color = PEN_COLORS.get(getattr(line.color, "name", str(line.color)), "#1a1a1a")
        base_w = PEN_WIDTHS.get(tool, 2.5)
        page.strokes.append(
            Stroke(
                # v6 x is centre-relative; y depends on page kind (see docstring)
                points=[(p.x + PAGE_WIDTH / 2 + ax, p.y + y_shift + ay) for p in line.points],
                color=color,
                width=max(0.5, base_w * (line.thickness_scale or 1.0)),
                highlighter=tool in HIGHLIGHTER_TOOLS,
                layer_visible=visible,
            )
        )

    def text_block(t: si.Text, ax: float, ay: float) -> TextBlock:
        block = TextBlock(
            text="",
            pos_x=t.pos_x + PAGE_WIDTH / 2 + ax,
            pos_y=t.pos_y + y_shift + ay,
            width=t.width,
        )
        try:
            doc = TextDocument.from_scene_item(t)
            height = 0.0
            for para in doc.contents:
                style = getattr(para.style, "value", None)
                style_name = getattr(style, "name", "PLAIN")
                block.paragraphs.append((style_name, str(para)))
                height += LINE_HEIGHTS.get(style_name, 70)
            block.text = "\n".join(text for _, text in block.paragraphs)
            block.height = height
        except Exception as exc:
            log.debug("styled text extraction failed: %s", exc)
            block.height = LINE_HEIGHTS["PLAIN"] * max(1, block.text.count("\n") + 1)
        return block

    if tree.root is not None:
        visit(tree.root, 0.0, 0.0, True)
    if tree.root_text is not None:
        page.texts.append(text_block(tree.root_text, 0.0, 0.0))

    if pdf_mode:
        # ink written while zoomed/scrolled extends past the page edges and
        # bottom; grow the canvas around the page so nothing is clipped
        xs = [x for s in page.strokes for x, _ in s.points]
        xs += [t.pos_x + t.width for t in page.texts]
        xs += [x + w for hl in page.highlights for x, _, w, _ in hl.rectangles]
        ys = [y for s in page.strokes for _, y in s.points]
        ys += [t.pos_y + t.height for t in page.texts]
        ys += [y + h for hl in page.highlights for _, y, _, h in hl.rectangles]
        if xs or ys:
            x_min = min([0.0] + xs) if xs else 0.0
            x_max = max([PAGE_WIDTH] + xs) if xs else PAGE_WIDTH
            shift = -x_min  # move everything right so left-overflow fits
            if shift:
                for s in page.strokes:
                    s.points = [(x + shift, y) for x, y in s.points]
                for t in page.texts:
                    t.pos_x += shift
                for hl in page.highlights:
                    hl.rectangles = [(x + shift, y, w, h) for x, y, w, h in hl.rectangles]
            page.origin = (shift, 0.0)
            height = max(ys) if ys else PAGE_HEIGHT
            page.content_size = (x_max + shift, max(PAGE_HEIGHT, height + TOP_MARGIN))
    else:
        _normalise(page)
    return page


def _normalise(page: PageModel) -> None:
    """Shift content onto the page: y starts at the top margin, narrower
    content is centred horizontally; compute the content size."""
    if not (page.strokes or page.texts):
        return
    xs: list[float] = []
    ys: list[float] = []
    for s in page.strokes:
        xs += [x for x, _ in s.points]
        ys += [y for _, y in s.points]
    for t in page.texts:
        xs += [t.pos_x, t.pos_x + t.width]
        ys += [t.pos_y, t.pos_y + getattr(t, "height", 70)]
    if not xs:
        return
    min_x, max_x = min(xs), max(xs)
    min_y = min(ys)
    dx = (PAGE_WIDTH - (max_x - min_x)) / 2 - min_x if (max_x - min_x) < PAGE_WIDTH else -min_x
    dy = TOP_MARGIN - min_y
    for s in page.strokes:
        s.points = [(x + dx, y + dy) for x, y in s.points]
    for t in page.texts:
        t.pos_x += dx
        t.pos_y += dy
    height = max(ys) + dy + TOP_MARGIN
    width = max_x + dx
    page.content_size = (max(PAGE_WIDTH, width), max(PAGE_HEIGHT, height))


def _parse_v5(data: bytes) -> PageModel:
    """Parse a 'version=5' .lines file (software 2.x).

    These files were extended mid-generation (software ~2.13 added a
    per-line id field and other records the classic spec lacks), so this
    is a tolerant scanner: it walks the buffer, validates each candidate
    line record hard (pen/width/colour/npts ranges + first point on the
    page), and skips four bytes past anything it doesn't recognise. A
    torn or exotic file still yields every stroke that survives.
    """
    page = PageModel()
    pos = 51
    size = len(data)
    while pos + 20 <= size:
        line = None
        for start in (pos, pos + 4):
            if start + 20 > size:
                continue
            pen, = struct.unpack_from("<i", data, start)
            width, = struct.unpack_from("<d", data, start + 4)
            colour, = struct.unpack_from("<i", data, start + 12)
            npts, = struct.unpack_from("<i", data, start + 16)
            if not (0 <= pen <= 23 and 0 <= colour <= 2):
                continue
            if not (0.15 <= width <= 60.0):
                continue
            if not (0 <= npts <= 20000) or start + 20 + npts * 24 > size:
                continue
            if npts:
                x, y = struct.unpack_from("<2f", data, start + 20)
                if not (-300 <= x <= 1800 and -300 <= y <= 2300):
                    continue
            line = (start, pen, width, colour, npts)
            break
        if line is None:
            pos += 4
            continue
        start, pen, width, colour, npts = line
        if npts:
            points = []
            for i in range(npts):
                x, y = struct.unpack_from("<2f", data, start + 20 + i * 24)
                points.append((x, y))
            page.strokes.append(
                Stroke(
                    points=points,
                    color=PEN_COLORS.get(V5_COLORS.get(colour, "BLACK"), "#1a1a1a"),
                    width=max(0.4, min(width, 60.0)),
                    highlighter=width > 8.0,
                )
            )
        pos = start + 20 + npts * 24
    return page


def page_text(page: PageModel) -> str:
    """All searchable text of a page: typed text plus PDF-highlight text."""
    parts = [t.text for t in page.texts if t.text]
    parts += [h.text for h in page.highlights if h.text]
    return "\n".join(parts).strip()
