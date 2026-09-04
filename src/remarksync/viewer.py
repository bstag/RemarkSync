"""Qt painting of reMarkable pages.

The painter draws a :class:`~remarksync.layout.PageFrame`: background,
base-document underlay or template, highlights, ink, typed text, then the
paper border and overflow dividers. It computes no geometry; every rect
and line comes from the frame.
"""

from __future__ import annotations

import math

import fitz  # pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget

from .layout import PageFrame, lay_out
from .backup import TEMPLATE_BLANK, TEMPLATE_DOTS, TEMPLATE_GRID, TEMPLATE_ISOMETRIC, TEMPLATE_RULED, DocItem, PageRef
from .rmdata import PageModel, Stroke, decode_page
from .textmetrics import (CHECKBOX_GAP, CHECKBOX_SIZE, FONT_SANS, FONT_SERIF, MARKER_GAP, TEXT_LEFT_PAD,
                          lay_out_lines, style)

_TEMPLATE_PEN = QColor(0xB3, 0xB3, 0xB3)


def draw_template(painter: QPainter, template: str, rect: QRectF) -> None:
    """Paint the page background pattern (dots / ruled / grid / isometric)."""
    if template == TEMPLATE_BLANK:
        return
    painter.save()
    painter.translate(rect.left(), rect.top())
    pen = QPen(_TEMPLATE_PEN, 1.5)
    painter.setPen(pen)
    spacing = 50.0

    if template == TEMPLATE_DOTS:
        y = spacing
        while y < rect.height():
            x = spacing
            while x < rect.width():
                painter.drawPoint(QPointF(x, y))
                x += spacing
            y += spacing
    elif template == TEMPLATE_RULED:
        y = spacing * 2
        while y < rect.height():
            painter.drawLine(QPointF(spacing, y), QPointF(rect.width() - spacing, y))
            y += spacing
    elif template == TEMPLATE_GRID:
        x = spacing
        while x < rect.width():
            painter.drawLine(QPointF(x, spacing), QPointF(x, rect.height() - spacing))
            x += spacing
        y = spacing
        while y < rect.height():
            painter.drawLine(QPointF(spacing, y), QPointF(rect.width() - spacing, y))
            y += spacing
    elif template == TEMPLATE_ISOMETRIC:
        step = spacing * 2
        y = rect.height() + step
        while y > -rect.width():
            painter.drawLine(QPointF(0, y), QPointF(rect.width(), y - rect.width()))
            painter.drawLine(QPointF(0, y - rect.width()), QPointF(rect.width(), y))
            y -= step
    painter.restore()


class PdfUnderlay:
    """Renders base-document pages to QImages at the size the frame asks for."""

    def __init__(self, pdf_path):
        self.doc = fitz.open(pdf_path)
        self._cache: dict[int, QImage] = {}

    @property
    def page_count(self) -> int:
        return self.doc.page_count

    def page_size(self, index: int) -> tuple[float, float] | None:
        """Base page size in PDF points (aspect ratio is what the layout uses)."""
        if index >= self.doc.page_count:
            return None
        rect = self.doc.load_page(index).rect
        return float(rect.width), float(rect.height)

    def page_image(self, index: int, size: tuple[float, float]) -> QImage | None:
        """The base page rendered to fill ``size`` (tablet pixels)."""
        if index in self._cache:
            return self._cache[index]
        if index >= self.doc.page_count:
            return None
        page = self.doc.load_page(index)
        zoom = max(size[0] / page.rect.width, size[1] / page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
        img = img.copy()  # detach from pymupdf buffer
        self._cache[index] = img
        return img

    def close(self) -> None:
        self.doc.close()


# Opaque pens at least this wide are filled as outlines; thinner ones are stroked.
OUTLINE_MIN_WIDTH = 5.0

# Segments whose widths round to the same step share one path. Opaque ink
# can afford fine steps; translucent ink (highlighter, marker) uses coarse
# ones because every run boundary is a spot where two round caps overlap
# and multiply-blend darker.
WIDTH_STEP_OPAQUE = 0.25
WIDTH_STEP_TRANSLUCENT = 2.0  # unused since translucent pens are filled as outlines; kept for the step rule


def _stroke_paths(stroke: Stroke) -> list[tuple[float, QPainterPath]]:
    """The stroke as (width, path) pairs: one path per distinct width, with
    consecutive same-width segments chained into one polyline, so a
    pressure-varying line costs a handful of draw calls, not one per point."""
    if stroke.uniform:
        path = QPainterPath(QPointF(*stroke.points[0]))
        for x, y in stroke.points[1:]:
            path.lineTo(QPointF(x, y))
        return [(stroke.widths[0] if stroke.widths else stroke.width, path)]
    step = WIDTH_STEP_TRANSLUCENT if stroke.highlighter else WIDTH_STEP_OPAQUE
    paths: dict[float, QPainterPath] = {}
    pts, ws = stroke.points, stroke.widths
    current: float | None = None
    for i in range(1, len(pts)):
        w = round((ws[i - 1] + ws[i]) / 2 / step) * step
        path = paths.get(w)
        if path is None:
            path = paths[w] = QPainterPath()
        if w != current:
            path.moveTo(QPointF(*pts[i - 1]))
            current = w
        path.lineTo(QPointF(*pts[i]))
    return sorted(paths.items())


def _decimated(stroke: Stroke) -> tuple[list[tuple[float, float]], list[float]]:
    """Points and widths with the stroke's sampling thinned to about a
    quarter of the pen width: the tablet samples every couple of pixels,
    which is far denser than a 30 px highlighter needs, and the outline
    cost is per point. Ends are always kept."""
    pts, ws = stroke.points, stroke.widths
    if len(pts) < 3:
        return pts, ws
    spacing = max(1.5, stroke.width / 4)
    keep_pts = [pts[0]]
    keep_ws = [ws[0]]
    lx, ly = pts[0]
    for i in range(1, len(pts) - 1):
        x, y = pts[i]
        if abs(x - lx) + abs(y - ly) >= spacing:
            keep_pts.append((x, y))
            keep_ws.append(ws[i])
            lx, ly = x, y
    keep_pts.append(pts[-1])
    keep_ws.append(ws[-1])
    return keep_pts, keep_ws


def _stroke_outline(stroke: Stroke) -> QPainterPath:
    """The area a variable-width stroke covers, as one winding-fill path,
    the way the tablet's own PDF export draws ink: a quad per segment whose
    width tapers from one point's width to the next, a wedge at every joint
    so turns leave no notch, and a round cap at each end. Filled once, so a
    translucent tint stays uniform however the pieces overlap, and abrupt
    width changes do not bulge the way per-point discs would."""
    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)
    pts, ws = _decimated(stroke)
    radii = [max(0.2, w / 2) for w in ws]
    normals: list[tuple[float, float] | None] = []

    def add(polygon: list[tuple[float, float]]) -> None:
        # every piece must wind the same way, or overlapping pieces of
        # opposite orientation cancel to holes under the winding rule
        area = 0.0
        for (ax_, ay_), (bx_, by_) in zip(polygon, polygon[1:] + polygon[:1]):
            area += ax_ * by_ - bx_ * ay_
        if area < 0:
            polygon = polygon[::-1]
        path.addPolygon(QPolygonF([QPointF(x_, y_) for x_, y_ in polygon]))
        path.closeSubpath()

    for i in range(1, len(pts)):
        (x0, y0), (x1, y1) = pts[i - 1], pts[i]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 1e-6:
            normals.append(None)
            continue
        nx, ny = -dy / length, dx / length
        normals.append((nx, ny))
        r0, r1 = radii[i - 1], radii[i]
        add([
            (x0 + nx * r0, y0 + ny * r0),
            (x1 + nx * r1, y1 + ny * r1),
            (x1 - nx * r1, y1 - ny * r1),
            (x0 - nx * r0, y0 - ny * r0),
        ])
    # joints: a wedge between the two segments' offset corners on each side
    for i in range(1, len(pts) - 1):
        a, b = normals[i - 1], normals[i]
        if a is None or b is None or a[0] * b[0] + a[1] * b[1] > 0.985:
            continue  # under ~10 degrees the notch is invisible
        x, y = pts[i]
        r = radii[i]
        for sign in (1.0, -1.0):
            add([
                (x, y),
                (x + sign * a[0] * r, y + sign * a[1] * r),
                (x + sign * b[0] * r, y + sign * b[1] * r),
            ])
    for idx in (0, len(pts) - 1):
        # round caps as polygons too, so their winding matches the rest
        (x, y), r = pts[idx], radii[idx]
        add([(x + r * math.cos(t * math.pi / 8), y + r * math.sin(t * math.pi / 8)) for t in range(16)])
    return path


class _PixmapCache:
    """A bounded, most-recently-used cache of painted pages. A heavy page
    (100k points) takes most of a second to paint; scrolling it back into
    view should be a blit."""

    def __init__(self, capacity: int = 16):
        self._capacity = capacity
        self._items: dict[tuple, QPixmap] = {}

    def get(self, key: tuple) -> QPixmap | None:
        pix = self._items.pop(key, None)
        if pix is not None:
            self._items[key] = pix  # most recent last
        return pix

    def put(self, key: tuple, pix: QPixmap) -> None:
        self._items.pop(key, None)
        self._items[key] = pix
        while len(self._items) > self._capacity:
            del self._items[next(iter(self._items))]

    def drop(self, owner: object) -> None:
        for key in [k for k in self._items if k[0] == owner]:
            del self._items[key]


_painted_pages = _PixmapCache()


def frame_for(doc: DocItem, page: PageRef, underlay: PdfUnderlay | None) -> PageFrame:
    """Decode and lay out one page of a document."""
    model = decode_page(page.rm_path) if page.rm_path else PageModel()
    base_size = underlay.page_size(page.index) if underlay is not None else None
    return lay_out(model, template=page.template, page_index=page.index, base_size=base_size)


class PageCanvas(QWidget):
    """One reMarkable page. Fixed size = frame canvas size * scale."""

    def __init__(self, doc: DocItem, page: PageRef, underlay: PdfUnderlay | None, scale: float = 1.0):
        super().__init__()
        self._doc = doc
        self._page = page
        self._underlay = underlay
        # lay out eagerly so the fixed size is correct before the first paint
        # (resizing during/after paint is unreliable in Qt)
        self._frame = frame_for(doc, page, underlay)
        self._scale = scale
        self.set_scale(scale)
        self.setToolTip(f"{doc.name} — page {page.index + 1}")

    def frame(self) -> PageFrame:
        return self._frame

    def model(self) -> PageModel:
        return self._frame.page

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        self.setFixedSize(int(self._frame.width * scale), int(self._frame.height * scale))
        self.update()

    def deleteLater(self) -> None:  # noqa: N802
        _painted_pages.drop(id(self))
        super().deleteLater()

    # -- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        key = (id(self), self._scale)
        pix = _painted_pages.get(key)
        if pix is None:
            pix = QPixmap(self.size())
            pix.fill(Qt.white)
            painter = QPainter(pix)
            self._paint_frame(painter)
            painter.end()
            _painted_pages.put(key, pix)
        QPainter(self).drawPixmap(0, 0, pix)

    def _paint_frame(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(self._scale, self._scale)

        frame = self._frame
        model = frame.page
        painter.fillRect(QRectF(0, 0, frame.width, frame.height), Qt.white)

        if frame.underlay is not None and self._underlay is not None:
            rect = QRectF(*frame.underlay)
            img = self._underlay.page_image(self._page.index, (rect.width(), rect.height()))
            if img is not None:
                painter.drawImage(rect, img)
        if frame.template is not None:
            draw_template(painter, frame.template, QRectF(*frame.paper))

        self._paint_highlights(painter, model)
        self._paint_strokes(painter, model)
        self._paint_texts(painter, model)

        painter.setPen(QPen(QColor(0, 0, 0, 30), 2))
        painter.drawRect(QRectF(0, 0, frame.width, frame.height))
        if frame.dividers:
            painter.setPen(QPen(QColor(0, 0, 0, 60), 2, Qt.DashLine))
            for (x0, y0), (x1, y1) in frame.dividers:
                painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))

    def _paint_highlights(self, painter: QPainter, model: PageModel) -> None:
        """Snap text highlights: translucent bands over the PDF text."""
        if not model.highlights:
            return
        painter.save()
        color = QColor("#ffd400")
        color.setAlpha(95)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.setCompositionMode(QPainter.CompositionMode_Multiply)
        for hl in model.highlights:
            for x, y, w, h in hl.rectangles:
                if w > 0 and h > 0:
                    painter.drawRect(QRectF(x, y, w, h))
        painter.restore()

    def _paint_strokes(self, painter: QPainter, model: PageModel) -> None:
        for stroke in model.strokes:
            if not stroke.layer_visible or len(stroke.points) < 2:
                continue
            color = QColor(stroke.color)
            painter.save()
            lighter = stroke.alpha < 1.0
            if stroke.highlighter:
                # translucent ink is filled as one outline so that no two
                # parts of the same stroke overlap and darken each other
                color.setAlphaF(stroke.alpha)
                painter.setCompositionMode(QPainter.CompositionMode_Multiply)
            elif lighter:
                color.setAlphaF(stroke.alpha)  # pencils, paintbrush: lighter than solid ink
            if stroke.highlighter or lighter or (not stroke.uniform and stroke.width >= OUTLINE_MIN_WIDTH):
                # wide or translucent ink: one filled outline (exact, no
                # overlaps); thin pens are cheaper as stroked paths and their
                # width steps are sub-pixel anyway
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawPath(_stroke_outline(stroke))
            else:
                pen = QPen(color)
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                for width, path in _stroke_paths(stroke):
                    pen.setWidthF(max(0.4, width))
                    painter.setPen(pen)
                    painter.drawPath(path)
            painter.restore()

    def _paint_texts(self, painter: QPainter, model: PageModel) -> None:
        """Typed text, line by line at the baselines the tablet uses (see
        textmetrics), in a serif or sans face sized per paragraph style,
        with list markers and checkboxes drawn in the indent."""
        if not model.texts:
            return
        painter.save()
        for block in model.texts:
            if not block.text.strip():
                continue
            lines = block.lines or lay_out_lines(block.paragraphs)
            counters: dict[str, int] = {}  # running numbers for numbered lists
            last_style = None
            for ln in lines:
                st = style(ln.style)
                if ln.first:
                    if st.marker in ("number", "letter"):
                        counters[st.name] = counters.get(st.name, 0) + 1 if last_style == st.name else 1
                    last_style = st.name
                x = block.pos_x + TEXT_LEFT_PAD + st.indent
                y = block.pos_y + ln.baseline
                font = QFont(FONT_SERIF if st.serif else FONT_SANS)
                font.setPixelSize(int(round(st.size)))
                font.setWeight(QFont.Medium if st.weight >= 500 else QFont.Normal)
                painter.setFont(font)
                painter.setPen(QColor("#1a1a1a"))
                if ln.text:
                    painter.drawText(QPointF(x, y), ln.text)
                if ln.first and st.marker:
                    self._paint_marker(painter, st.marker, x, y, st.size, counters.get(st.name, 1))
        painter.restore()

    @staticmethod
    def _paint_marker(painter: QPainter, marker: str, x: float, baseline: float, size: float, number: int) -> None:
        if marker in ("bullet", "bullet2"):
            painter.drawText(QPointF(x - MARKER_GAP, baseline), "\u2022" if marker == "bullet" else "\u25e6")
        elif marker in ("number", "letter"):
            label = f"{number}." if marker == "number" else f"{chr(96 + ((number - 1) % 26) + 1)}."
            width = painter.fontMetrics().horizontalAdvance(label)
            painter.drawText(QPointF(x - MARKER_GAP + 12 - width, baseline), label)
        elif marker in ("box", "box_checked"):
            top = baseline - CHECKBOX_SIZE
            rect = QRectF(x - CHECKBOX_GAP, top, CHECKBOX_SIZE, CHECKBOX_SIZE)
            painter.save()
            painter.setPen(QPen(QColor("#1a1a1a"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
            if marker == "box_checked":
                painter.drawLine(QPointF(rect.left() + 5, rect.top() + 13), QPointF(rect.left() + 11, rect.bottom() - 5))
                painter.drawLine(QPointF(rect.left() + 11, rect.bottom() - 5), QPointF(rect.right() - 4, rect.top() + 5))
            painter.restore()
