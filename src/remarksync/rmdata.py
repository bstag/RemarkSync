"""Decode .rm page files into a pure-Python page model.

This is the *page decoder*: bytes in, a ``PageModel`` out, in **page
coordinates** (x from the left edge of the tablet page, y from its top,
both in tablet pixels). Nothing here knows where the page will be drawn,
whether the canvas should grow, or which template sits behind it; that is
the job of :mod:`remarksync.layout`. No Qt dependencies here.

Two adapters sit behind the one interface:

* v6 (software 3.x) — parsed by ``rmscene``; x is stored centre-relative
  and shifted here so every caller sees the same frame.
* v5 (software 2.x "version=5" .lines) — a tolerant scanner over the
  binary layout, which was extended in place around firmware 2.13.
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

from .textmetrics import LINE_HEIGHTS, TEXT_TOP_Y, paragraphs_height

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

# Fallback stroke widths in tablet pixels for each pen, before
# thickness_scale. v6 files carry the tablet's own computed width on every
# point (pressure, tilt and the thickness setting already applied), and
# that is what gets drawn; these only stand in when a file has none.
PEN_WIDTHS: dict[str, float] = {
    "BALLPOINT_1": 2.2,
    "BALLPOINT_2": 2.2,
    "FINELINER_1": 4.5,
    "FINELINER_2": 4.5,
    "CALIGRAPHY": 3.5,  # rmscene spells it this way; keep both
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

V5_HEADER = b"reMarkable .lines file, version=5"
V5_COLORS = {0: "BLACK", 1: "GRAY", 2: "WHITE"}


@dataclass
class Stroke:
    """A pen stroke: polyline in page coordinates.

    ``widths`` holds the stroke width at every point, in tablet pixels, as
    the tablet computed it from pressure, tilt, speed and the pen's
    thickness setting; it has one entry per point. ``width`` is their mean,
    for callers that want one number.
    """

    points: list[tuple[float, float]]
    color: str  # hex
    width: float  # tablet px, mean of ``widths``
    highlighter: bool = False
    layer_visible: bool = True
    widths: list[float] = field(default_factory=list)
    tool: str = ""

    def __post_init__(self) -> None:
        if not self.widths:
            self.widths = [self.width] * len(self.points)

    @property
    def uniform(self) -> bool:
        """True when every point has the same width."""
        return len(set(self.widths)) <= 1


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
    """Everything on one page, in page coordinates. Positions are exactly
    where the tablet put them; see :mod:`remarksync.layout` for where the
    page ends up on a canvas."""

    strokes: list[Stroke] = field(default_factory=list)
    texts: list[TextBlock] = field(default_factory=list)
    highlights: list[GlyphHighlight] = field(default_factory=list)
    error: str | None = None

    @property
    def has_content(self) -> bool:
        return bool(self.strokes or self.texts or self.highlights)


def decode_page(source: Path | bytes) -> PageModel:
    """Decode a .rm file (path or raw bytes) into a PageModel.

    Never raises for bad input: an unparseable page comes back empty with
    ``error`` set so the caller stays usable.
    """
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        data = Path(source).read_bytes()

    if data[:33] == V5_HEADER:
        return _decode_v5(data)

    try:
        tree = read_tree(io.BytesIO(data))
    except Exception as exc:
        log.warning("could not parse %s: %s", source if isinstance(source, Path) else "<bytes>", exc)
        return PageModel(error=str(exc))
    return _decode_v6(tree)


def page_text(page: PageModel) -> str:
    """All searchable text of a page: typed text plus PDF-highlight text."""
    parts = [t.text for t in page.texts if t.text]
    parts += [h.text for h in page.highlights if h.text]
    return "\n".join(parts).strip()


# ---------------------------------------------------------------- v6 adapter


def _visible(group: si.Group) -> bool:
    try:
        return group.visible.value is not False
    except Exception:
        return True


def _anchor_positions(root_text) -> dict:
    """Vertical position (raw coords) of each text-line anchor: the top of
    the first line box, then accumulated per-paragraph line heights. Ink
    layers pinned to a line move with it."""
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


def _decode_v6(tree) -> PageModel:
    """v6 stores x centre-relative and y from the page top, for ink and
    typed text alike (verified against the tablet's own PDF export, see
    ADR-0001); shift x so the model is in page coordinates."""
    page = PageModel()
    anchor_pos = _anchor_positions(tree.root_text)
    x_shift = PAGE_WIDTH / 2

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
                    rectangles=[(x + x_shift, y, w, h) for x, y, w, h in rects],
                )
            )
        elif isinstance(item, si.Text):
            page.texts.append(text_block(item, ax, ay))

    def add_line(line: si.Line, ax: float, ay: float, visible: bool) -> None:
        tool = getattr(line.tool, "name", str(line.tool))
        if tool in ERASER_TOOLS:
            return
        color = PEN_COLORS.get(getattr(line.color, "name", str(line.color)), "#1a1a1a")
        widths = _point_widths(line, tool)
        page.strokes.append(
            Stroke(
                points=[(p.x + x_shift + ax, p.y + ay) for p in line.points],
                color=color,
                width=sum(widths) / len(widths) if widths else 0.5,
                highlighter=tool in HIGHLIGHTER_TOOLS,
                layer_visible=visible,
                widths=widths,
                tool=tool,
            )
        )

    def text_block(t: si.Text, ax: float, ay: float) -> TextBlock:
        block = TextBlock(text="", pos_x=t.pos_x + x_shift + ax, pos_y=t.pos_y + ay, width=t.width)
        try:
            doc = TextDocument.from_scene_item(t)
            for para in doc.contents:
                style = getattr(para.style, "value", None)
                style_name = getattr(style, "name", "PLAIN")
                block.paragraphs.append((style_name, str(para)))
            block.text = "\n".join(text for _, text in block.paragraphs)
            block.height = paragraphs_height(style for style, _ in block.paragraphs)
        except Exception as exc:
            log.debug("styled text extraction failed: %s", exc)
            block.height = LINE_HEIGHTS["PLAIN"] * max(1, block.text.count("\n") + 1)
        return block

    if tree.root is not None:
        visit(tree.root, 0.0, 0.0, True)
    if tree.root_text is not None:
        page.texts.append(text_block(tree.root_text, 0.0, 0.0))
    return page


def _point_widths(line: si.Line, tool: str) -> list[float]:
    """Per-point stroke widths in tablet px. rmscene stores each point's
    width as ``int(round(raw * 4))``, so ``/ 4`` restores the tablet's
    value; a file with no widths falls back to the pen table."""
    widths = [max(0.5, float(p.width) / 4.0) for p in line.points if p.width]
    if len(widths) == len(line.points):
        return widths
    fallback = max(0.5, PEN_WIDTHS.get(tool, 2.5) * (line.thickness_scale or 1.0))
    return [fallback] * len(line.points)


# ---------------------------------------------------------------- v5 adapter


def _decode_v5(data: bytes) -> PageModel:
    """Decode a 'version=5' .lines file (software 2.x).

    These files were extended mid-generation (software ~2.13 added a
    per-line id field and other records the classic spec lacks), so this
    is a tolerant scanner: it walks the buffer, validates each candidate
    line record hard (pen/width/colour/npts ranges + first point on the
    page), and skips four bytes past anything it doesn't recognise. A
    torn or exotic file still yields every stroke that survives. v5 points
    are already in page coordinates.
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
                    tool=f"V5_PEN_{pen}",
                )
            )
        pos = start + 20 + npts * 24
    return page
