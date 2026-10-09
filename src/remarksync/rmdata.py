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

from rmscene import CrdtId, read_tree
from rmscene import scene_items as si
from rmscene.text import TextDocument

from . import rmformat
from .textmetrics import TEXT_WIDTH, TextLine, block_height, lay_out_lines, style_name

rmformat.install()  # read the paragraph-format fields rmscene skips (subheading vs bold)


def _unknown_paragraph_style(cls, value):
    # software 3.x writes paragraph style codes rmscene 0.8 has no name for
    # (numbered lists, nested checkboxes); keep the code instead of dropping
    # the paragraph's style
    member = int.__new__(cls, value)
    member._name_ = f"STYLE_{value}"
    member._value_ = value
    return member


si.ParagraphStyle._missing_ = classmethod(_unknown_paragraph_style)

log = logging.getLogger(__name__)

# reMarkable 2 portrait page size in tablet pixels. This is the default, not the
# only one: a page's own SceneInfo carries its paper size, and a Paper Pro
# writes 1620x2160 there. A backup can hold both, one device having replaced
# the other -- the reference backup has 163 pages of the first and 18 of the
# second.
PAGE_WIDTH = 1404
PAGE_HEIGHT = 1872
# The papers we can name, largest last. Typed text sits in the same place on
# either: a block's pos_y is 234 and its width is one of the same three
# settings, with pos_x stored relative to the centre so it centres itself.
PAPER_SIZES: dict[tuple[float, float], str] = {
    (1404, 1872): "reMarkable 1 and 2",
    (1620, 2160): "reMarkable Paper Pro",
}

# reMarkable accent colours (sRGB approximations of the tablet palette).
PEN_COLORS: dict[str, str] = {
    "BLACK": "#1a1a1a",
    "GRAY": "#808080",
    "WHITE": "#ffffff",
    "YELLOW": "#f2b800",
    "GREEN": "#00a651",
    "PINK": "#e91e63",
    "BLUE": "#0072ce",
    "RED": "#c23132",  # as the tablet's own PDF export renders it
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

# Pens the tablet blends translucently over the page. The marker is an
# opaque felt tip and is not one of them.
TRANSLUCENT_TOOLS = {"HIGHLIGHTER_1", "HIGHLIGHTER_2", "SHADER"}
HIGHLIGHTER_TOOLS = TRANSLUCENT_TOOLS  # older name
ERASER_TOOLS = {"ERASER", "ERASER_AREA"}
# Tint used for translucent pens whose file carries no alpha of its own.
DEFAULT_TRANSLUCENT_ALPHA = 110 / 255

# Opaque pens the tablet draws lighter than solid ink. Fitted on 2026-09-04
# by comparing ink mass per pen against the tablet's own PDF export of a
# page with every pen (ratio of export to solid rendering at the stored
# width): pencils ~0.68-0.74, paintbrush ~0.81. The grainy pencil texture
# itself is not modelled; this matches its average darkness.
PEN_OPACITY: dict[str, float] = {
    "PENCIL_1": 0.7,
    "PENCIL_2": 0.7,
    "MECHANICAL_PENCIL_1": 0.73,
    "MECHANICAL_PENCIL_2": 0.73,
    "PAINTBRUSH_1": 0.82,
    "PAINTBRUSH_2": 0.82,
}
# The marker is a chisel tip: its stored per-point width is the nib's full
# extent, and the ribbon it actually lays down carries ~15% less ink.
PEN_WIDTH_FACTOR: dict[str, float] = {"MARKER_1": 0.85, "MARKER_2": 0.85}

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
    color: str  # hex, the tablet's own RGB when the file carries one
    width: float  # tablet px, mean of ``widths``
    highlighter: bool = False  # translucent pen: blended over the page
    layer_visible: bool = True
    widths: list[float] = field(default_factory=list)
    tool: str = ""
    alpha: float = 1.0  # opacity for translucent pens (the file's, or the default tint)
    layer_id: str = ""

    def __post_init__(self) -> None:
        if not self.widths:
            self.widths = [self.width] * len(self.points)

    @property
    def uniform(self) -> bool:
        """True when every point has the same width."""
        return len(set(self.widths)) <= 1


@dataclass
class TextBlock:
    """Typed text block in page coordinates (top-left corner + width).

    ``paragraphs`` are (style, text) as typed; ``lines`` are what the tablet
    draws: each paragraph wrapped into lines with a baseline (relative to
    ``pos_y``), the way :mod:`textmetrics` models the device's layout.
    """

    text: str
    pos_x: float
    pos_y: float
    width: float
    height: float = 70.0
    paragraphs: list[tuple[str, str]] = field(default_factory=list)  # (style, text)
    lines: list[TextLine] = field(default_factory=list)
    layer_visible: bool = True
    layer_id: str = ""


@dataclass
class Highlight:
    """Text highlighted inside a base PDF page (search gold)."""

    text: str
    rectangles: list[tuple[float, float, float, float]] = field(default_factory=list)
    color: str = "#ffd400"
    alpha: float = 95 / 255
    layer_visible: bool = True
    layer_id: str = ""


@dataclass
class PageModel:
    """Everything on one page, in page coordinates. Positions are exactly
    where the tablet put them; see :mod:`remarksync.layout` for where the
    page ends up on a canvas."""

    strokes: list[Stroke] = field(default_factory=list)
    texts: list[TextBlock] = field(default_factory=list)
    highlights: list[Highlight] = field(default_factory=list)
    error: str | None = None
    # The paper this page was drawn on, in tablet px, when the file says so.
    # A Paper Pro page declares 1620x2160 where the rM2 sheet is 1404x1872,
    # and one notebook can hold pages of each. None means the file is silent
    # and the reader should assume the default sheet; choosing that default is
    # the layout's business, not the decoder's.
    paper_size: tuple[float, float] | None = None

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
        try:
            data = Path(source).read_bytes()
        except OSError as exc:
            return PageModel(error=f"{Path(source).name} could not be read ({exc})")

    if data[:33] == V5_HEADER:
        return _decode_v5(data)

    try:
        tree = read_tree(io.BytesIO(data))
    except Exception as exc:
        # rmscene rejects a malformed stream with a bare assert, whose str() is
        # empty, so the type has to carry the message or the reader is told
        # nothing at all.
        reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        where = source.name if isinstance(source, Path) else "<bytes>"
        log.warning("could not parse %s: %s", where, reason)
        return PageModel(error=f"{where} is not a readable v6 page ({reason})")
    try:
        return _decode_v6(tree)
    except Exception as exc:
        return PageModel(error=f"page scene could not be decoded ({type(exc).__name__}: {exc})")


def page_text(page: PageModel) -> str:
    """All searchable text of a page: typed text plus PDF-highlight text."""
    parts = [t.text for t in page.texts if t.text and t.layer_visible]
    parts += [h.text for h in page.highlights if h.text and h.layer_visible]
    return "\n".join(parts).strip()


# ---------------------------------------------------------------- v6 adapter


def _visible(group: si.Group) -> bool:
    try:
        return group.visible.value is not False
    except Exception:
        return True


def _paragraphs(doc: TextDocument) -> list[tuple[str, str]]:
    """(style name, text) per paragraph, as the tablet counts them. rmscene
    drops an empty first paragraph when the text starts with a newline
    (the file's line count is one higher than its paragraph count); that
    line is restored so everything below it lands where the device puts it."""
    paras = []
    for para in doc.contents:
        code = getattr(para.style, "value", None)
        paras.append((style_name(int(code)) if code is not None else "PLAIN", str(para)))
    if doc.contents and doc.contents[0].start_id != CrdtId(0, 0):
        paras.insert(0, ("PLAIN", ""))
    return paras


def _anchor_positions(root_text) -> dict:
    """Raw y of each text character's line baseline, which is what an
    anchored ink layer is pinned to. A paragraph's start id is the newline
    ending the previous paragraph, so it rides that paragraph's last line."""
    pos = {}
    if root_text is None:
        return pos
    try:
        doc = TextDocument.from_scene_item(root_text)
        paras = _paragraphs(doc)
        # anchored ink is pinned to these baselines, so they have to be the
        # baselines of *this* block's width, not of the usual one
        lines = lay_out_lines(paras, getattr(root_text, "width", None) or TEXT_WIDTH)
        offset = len(paras) - len(doc.contents)  # 1 when a hidden first line was restored
        by_para: dict[int, list[TextLine]] = {}
        for ln in lines:
            by_para.setdefault(ln.paragraph, []).append(ln)
        previous_last = None
        for i, para in enumerate(doc.contents):
            k = i + offset
            plines = by_para.get(k) or []
            # the start id sits at the end of the previous paragraph's last line
            if previous_last is not None:
                pos[para.start_id] = root_text.pos_y + previous_last.baseline
            elif plines:
                pos[para.start_id] = root_text.pos_y + plines[0].baseline
            n = 0
            for sub in para.contents:
                for char_id in sub.i:
                    line = next((ln for ln in plines if n < ln.chars), plines[-1] if plines else None)
                    if line is not None:
                        pos[char_id] = root_text.pos_y + line.baseline
                    n += 1
            previous_last = plines[-1] if plines else previous_last
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
    paper_size = getattr(tree.scene_info, "paper_size", None) if tree.scene_info is not None else None
    page = PageModel(paper_size=paper_size)
    anchor_pos = _anchor_positions(tree.root_text)
    # x is relative to the centre of *this page's* paper, so a Paper Pro page
    # shifts by 810, not the 702 of the smaller sheet.
    x_shift = (paper_size[0] if paper_size else PAGE_WIDTH) / 2

    def visit(item: si.SceneItem, ax: float, ay: float, visible: bool, layer_id: str = "") -> None:
        if isinstance(item, si.Group):
            vis = visible and _visible(item)
            gx, gy = _group_anchor(item, anchor_pos)
            for child in item.children.values():
                visit(child, ax + gx, ay + gy, vis, str(item.node_id))
        elif isinstance(item, si.Line):
            add_line(item, ax, ay, visible, layer_id)
        elif isinstance(item, si.GlyphRange):
            rects = [(r.x, r.y, r.w, r.h) for r in item.rectangles]
            if rects:
                # the tablet stores a small sub-line-height rectangle for a
                # leading verse number but does not render it; match that
                # by dropping rects much shorter than the highlight's bands
                band_h = max(h for *_x, _y, _w, h in rects)
                rects = [r for r in rects if r[3] >= 0.75 * band_h]
            page.highlights.append(
                Highlight(
                    text=item.text or "",
                    rectangles=[(x + x_shift + ax, y + ay, w, h) for x, y, w, h in rects],
                    color=_glyph_color(item),
                    alpha=(item.color_rgba[3] / 255 if item.color_rgba and item.color_rgba[3] < 255 else 95 / 255),
                    layer_visible=visible,
                    layer_id=layer_id,
                )
            )
        elif isinstance(item, si.Text):
            page.texts.append(text_block(item, ax, ay, visible, layer_id))

    def add_line(line: si.Line, ax: float, ay: float, visible: bool, layer_id: str) -> None:
        tool = getattr(line.tool, "name", str(line.tool))
        if tool in ERASER_TOOLS:
            return
        color, alpha = _line_color(line, tool)
        if alpha == 1.0:
            alpha = PEN_OPACITY.get(tool, 1.0)
        widths = _point_widths(line, tool)
        factor = PEN_WIDTH_FACTOR.get(tool)
        if factor:
            widths = [w * factor for w in widths]
        page.strokes.append(
            Stroke(
                points=[(p.x + x_shift + ax, p.y + ay) for p in line.points],
                color=color,
                width=sum(widths) / len(widths) if widths else 0.5,
                highlighter=tool in TRANSLUCENT_TOOLS,
                layer_visible=visible,
                widths=widths,
                tool=tool,
                alpha=alpha,
                layer_id=layer_id,
            )
        )

    def text_block(t: si.Text, ax: float, ay: float, visible: bool = True, layer_id: str = "") -> TextBlock:
        block = TextBlock(text="", pos_x=t.pos_x + x_shift + ax, pos_y=t.pos_y + ay, width=t.width,
                          layer_visible=visible, layer_id=layer_id)
        try:
            doc = TextDocument.from_scene_item(t)
            block.paragraphs = _paragraphs(doc)
            block.text = "\n".join(text for _, text in block.paragraphs)
            block.lines = lay_out_lines(block.paragraphs, block.width)
            block.height = block_height(block.paragraphs, block.width)
        except Exception as exc:
            log.debug("styled text extraction failed: %s", exc)
            block.height = 70.0 * max(1, block.text.count("\n") + 1)
        return block

    if tree.root is not None:
        visit(tree.root, 0.0, 0.0, True)
    if tree.root_text is not None:
        page.texts.append(text_block(tree.root_text, 0.0, 0.0))
    return page


def _glyph_color(item: si.GlyphRange) -> str:
    # Confirmed live yellow glyphs carry opaque black as an RGBA placeholder.
    # Only this contradictory pair uses the palette; genuine custom RGBA and
    # partially transparent black remain authoritative.
    if item.color == si.PenColor.YELLOW and item.color_rgba == (0, 0, 0, 255):
        return PEN_COLORS["YELLOW"]
    if item.color_rgba:
        r, g, b, _a = item.color_rgba
        return f"#{r:02x}{g:02x}{b:02x}"
    return PEN_COLORS.get(getattr(item.color, "name", ""), "#ffd400")


def _line_color(line: si.Line, tool: str) -> tuple[str, float]:
    """(hex colour, alpha) for a line. Software 3.x writes an RGBA per line
    (six highlighter colours, the shader's grey at 25%); older files only
    have the palette id, mapped through PEN_COLORS."""
    rgba = getattr(line, "color_rgba", None)
    translucent = tool in TRANSLUCENT_TOOLS
    if rgba:
        r, g, b, a = rgba
        hex_color = f"#{r:02x}{g:02x}{b:02x}"
        # highlighters are stored fully opaque and tinted by the tablet at
        # draw time; only a real partial alpha (the shader) is kept
        alpha = a / 255 if (translucent and a < 255) else (DEFAULT_TRANSLUCENT_ALPHA if translucent else 1.0)
        return hex_color, alpha
    name = getattr(line.color, "name", str(line.color))
    return PEN_COLORS.get(name, "#1a1a1a"), (DEFAULT_TRANSLUCENT_ALPHA if translucent else 1.0)


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


# A v5 line record: pen, the pen's thickness setting, colour, point count.
V5_LINE = struct.Struct("<idii")
# A v5 point: the same six fields rmscene documents for v6, as float32.
V5_POINT = struct.Struct("<6f")
# Every line record is preceded by one int32 the classic v5 spec omits.
# This is the "extra record" software ~2.13 added; it is what made a
# tolerant scanner look necessary.
V5_LINE_PREFIX = 4
# Widths outside this range are not a pen width; fall back to the line's own.
V5_WIDTH_RANGE = (0.15, 60.0)
# Mean width above which a v5 stroke is treated as translucent. There is no
# reliable pen table for this file variant -- the ids seen in real files
# (0, 2, 5) do not line up with the classic spec, and pen 2 carries 57 px
# strokes -- so width remains the only honest discriminator.
V5_TRANSLUCENT_WIDTH = 8.0
# Mean width above which a v5 stroke is the eraser rather than a pen.
#
# v5 records an erase as a stroke of its own, and the tablet ignores those
# records when it renders: whatever survived the erase is already what the
# file still holds, so drawing them paints bars over the surviving writing.
# Pen ids cannot tell them apart, because pen 0 is both the writing pen at
# 2.96-4.44 px and, at 40 px, the eraser; pen 2 erases at 14.4-57.6 px.
# Width can, and the gap is wide. Fitted 2026-09-17 against the tablet's own
# PDF export of five documents: dropping above 20 px brings our ink mass to
# 1.045 of the device's with no page made worse, where 8 px also deleted
# genuine broad strokes and wrecked two pages (one fell to 0.02).
V5_ERASER_WIDTH = 20.0


def _v5_stroke(data: bytes, offset: int, npts: int, pen: int, base_width: float, colour: int) -> Stroke | None:
    """One v5 line as a Stroke, at the width stored on every point, or
    ``None`` when the line is an erase rather than ink.

    Each point carries (x, y, speed, direction, width, pressure) -- the
    field order rmscene documents for v6 -- and that width is already in
    tablet pixels, so it is used as it stands. The line's own width is only
    the pen's thickness setting (2.0, 2.4 or 3.0 in real files) and serves
    as the fallback.
    """
    low, high = V5_WIDTH_RANGE
    if not low <= base_width <= high:  # a thickness setting we do not recognise
        base_width = min(max(base_width, low), high)
    points: list[tuple[float, float]] = []
    widths: list[float] = []
    for i in range(npts):
        x, y, _speed, _direction, width, _pressure = V5_POINT.unpack_from(data, offset + i * V5_POINT.size)
        points.append((x, y))
        widths.append(width if low <= width <= high else base_width)
    mean = sum(widths) / len(widths) if widths else base_width
    if mean > V5_ERASER_WIDTH:
        return None  # an erase, not ink; see V5_ERASER_WIDTH
    return Stroke(
        points=points,
        color=PEN_COLORS.get(V5_COLORS.get(colour, "BLACK"), "#1a1a1a"),
        width=max(0.4, min(mean, high)),
        highlighter=mean > V5_TRANSLUCENT_WIDTH,
        widths=[max(0.4, min(w, high)) for w in widths],
        tool=f"V5_PEN_{pen}",
    )


def _decode_v5(data: bytes) -> PageModel:
    """Decode a 'version=5' .lines file (software 2.x).

    The structure the header declares is read first. Every one of the 44 v5
    pages in the reference backup parses that way and is consumed to the
    byte, so a well-formed file never needs guesswork. A file that does not
    match falls back to :func:`_scan_v5`.

    v5 points are already in page coordinates.
    """
    page = _decode_v5_declared(data)
    return page if page is not None else _scan_v5(data)


def _decode_v5_declared(data: bytes) -> PageModel | None:
    """Read the layers and lines the header declares.

    Layout: the 43-byte magic, an int32 layer count, then per layer an
    int32 line count followed by that many lines, each one an int32 prefix
    then :data:`V5_LINE` then its points.

    Returns ``None`` unless the structure accounts for every byte, which is
    the signal that the framing was understood rather than merely tolerated.
    Note the count at offset 47 belongs to the *first* layer only, so a
    multi-layer page legitimately holds more lines than it declares there.
    """
    size = len(data)
    pos = 43
    if pos + 4 > size:
        return None
    layers, = struct.unpack_from("<i", data, pos)
    pos += 4
    if not 1 <= layers <= 16:
        return None

    low, high = V5_WIDTH_RANGE
    page = PageModel()
    for _ in range(layers):
        if pos + 4 > size:
            return None
        count, = struct.unpack_from("<i", data, pos)
        pos += 4
        if not 0 <= count <= 20000:
            return None
        for _ in range(count):
            if pos + V5_LINE_PREFIX + V5_LINE.size > size:
                return None
            pen, width, colour, npts = V5_LINE.unpack_from(data, pos + V5_LINE_PREFIX)
            start = pos + V5_LINE_PREFIX + V5_LINE.size
            # Only structural impossibilities disqualify the framing. An odd
            # pen id or width is a value to sanitise, not evidence that the
            # layout was misread -- consuming every byte is that evidence.
            if not 0 <= npts <= 20000 or start + npts * V5_POINT.size > size:
                return None
            if npts:
                stroke = _v5_stroke(data, start, npts, pen, width, colour)
                if stroke is not None:
                    page.strokes.append(stroke)
            pos = start + npts * V5_POINT.size
    return page if pos == size else None


def _scan_v5(data: bytes) -> PageModel:
    """Last resort for a v5 file whose framing does not add up.

    Walks the buffer, validates each candidate line record hard (pen,
    width, colour and point-count ranges plus a first point on the page)
    and skips four bytes past anything it does not recognise, so a torn
    file still yields the strokes that survive.

    Only reachable for damaged files: this recovers most strokes but can
    also mistake point data for a line header, so it is no longer the
    primary path.
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
            stroke = _v5_stroke(data, start + V5_LINE.size, npts, pen, width, colour)
            if stroke is not None:
                page.strokes.append(stroke)
        pos = start + V5_LINE.size + npts * V5_POINT.size
    return page

