"""Qt painting of reMarkable pages.

The painter draws a :class:`~remarksync.layout.PageFrame`: background,
base-document underlay or template, highlights, ink, typed text, then the
paper border and overflow dividers. It computes no geometry; every rect
and line comes from the frame.
"""

from __future__ import annotations

import fitz  # pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget

from .inkgeometry import stroke_polygons
from .layout import PageFrame, placeholder_frame
from .backup import TEMPLATE_BLANK, TEMPLATE_DOTS, TEMPLATE_GRID, TEMPLATE_ISOMETRIC, TEMPLATE_RULED, DocItem, PageRef
from .rmdata import PageModel, Stroke
from .textmetrics import (CHECK_MARK, CHECKBOX_GAP, CHECKBOX_SIZE, FONT_SANS, FONT_SERIF, MARKER_GAP,
                          NUMBER_MARKER_NUDGE, TEXT_LEFT_PAD, lay_out_lines, marker_label, numbered)

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

    def __init__(self, pdf_path, cache_bytes: int = 64 * 1024 * 1024):
        self.doc = fitz.open(pdf_path)
        self._cache: dict[tuple, QImage] = {}
        self._cache_bytes = cache_bytes

    def page_size(self, index: int | None) -> tuple[float, float] | None:
        """Base page size in PDF points (aspect ratio is what the layout uses)."""
        if index is None or not 0 <= index < self.doc.page_count:
            return None
        rect = self.doc.load_page(index).rect
        return float(rect.width), float(rect.height)

    def page_image(self, index: int | None, size: tuple[float, float]) -> QImage | None:
        """The base page rendered to fill ``size`` (tablet pixels)."""
        key = (index, size)
        if key in self._cache:
            img = self._cache.pop(key)
            self._cache[key] = img
            return img
        if index is None or not 0 <= index < self.doc.page_count:
            return None
        page = self.doc.load_page(index)
        zoom = max(size[0] / page.rect.width, size[1] / page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
        img = img.copy()  # detach from pymupdf buffer
        self._cache[key] = img
        while self._cache and sum(image.sizeInBytes() for image in self._cache.values()) > self._cache_bytes:
            del self._cache[next(iter(self._cache))]
        return img

    def close(self) -> None:
        self._cache.clear()
        self.doc.close()


# Opaque pens at least this wide are filled as outlines; thinner ones are stroked.
OUTLINE_MIN_WIDTH = 5.0

# Segments whose widths round to the same step share one path. Only opaque
# ink is stroked this way, and it can afford fine steps; translucent ink is
# filled as one outline instead, because every run boundary would be a spot
# where two round caps overlap and multiply-blend darker.
WIDTH_STEP_OPAQUE = 0.25


def _stroke_paths(stroke: Stroke) -> list[tuple[float, QPainterPath]]:
    """The stroke as (width, path) pairs: one path per distinct width, with
    consecutive same-width segments chained into one polyline, so a
    pressure-varying line costs a handful of draw calls, not one per point."""
    if stroke.uniform:
        path = QPainterPath(QPointF(*stroke.points[0]))
        for x, y in stroke.points[1:]:
            path.lineTo(QPointF(x, y))
        return [(stroke.widths[0] if stroke.widths else stroke.width, path)]
    step = WIDTH_STEP_OPAQUE
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


def _stroke_outline(stroke: Stroke) -> QPainterPath:
    """Shared variable-width ink geometry, filled as one compound path."""
    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)
    for polygon in stroke_polygons(stroke):
        path.addPolygon(QPolygonF([QPointF(x, y) for x, y in polygon]))
        path.closeSubpath()
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


class PageCanvas(QWidget):
    """One reMarkable page. Fixed size = frame canvas size * scale.

    Built with a placeholder frame (an empty page of the right paper size)
    so a document opens at once; ``set_frame`` swaps the decoded page in
    when a worker delivers it, or pass ``frame`` to build it decoded.
    """

    def __init__(self, doc: DocItem, page: PageRef, underlay: PdfUnderlay | None, scale: float = 1.0,
                 frame: PageFrame | None = None):
        super().__init__()
        self._doc = doc
        self._page = page
        self._underlay = underlay
        self._decoded = frame is not None
        base_size = underlay.page_size(page.base_index) if underlay is not None else None
        self._frame = frame if frame is not None else placeholder_frame(page, base_size)
        self._scale = scale
        self.set_scale(scale)
        self._update_tooltip()

    def frame(self) -> PageFrame:
        return self._frame

    @property
    def decoded(self) -> bool:
        """False while the canvas still shows the placeholder."""
        return self._decoded

    @property
    def page_index(self) -> int:
        return self._page.index

    def set_frame(self, frame: PageFrame) -> None:
        """Swap in the decoded page (sizes the widget, drops the cached paint)."""
        self._frame = frame
        self._decoded = True
        self._update_tooltip()
        _painted_pages.drop(id(self))
        self.set_scale(self._scale)

    def _update_tooltip(self) -> None:
        label = f"{self._doc.name} — page {self._page.index + 1}"
        if self._frame.page.error:
            label += f"\n{self._frame.page.error}"
        self.setToolTip(label)

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        self.setFixedSize(max(1, int(self._frame.width * scale)), max(1, int(self._frame.height * scale)))
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
            img = self._underlay.page_image(self._page.base_index, (rect.width(), rect.height()))
            if img is not None:
                painter.drawImage(rect, img)
        if frame.template is not None:
            draw_template(painter, frame.template, QRectF(*frame.paper))

        self._paint_highlights(painter, model)
        self._paint_strokes(painter, model)
        self._paint_texts(painter, model)
        if model.error:
            painter.setPen(QColor("#a02020"))
            font = QFont(FONT_SANS)
            font.setPixelSize(26)
            painter.setFont(font)
            painter.drawText(QRectF(24, 24, max(1, frame.width - 48), 200),
                             Qt.TextWordWrap, f"Unreadable page\n{model.error}")

        painter.setPen(QPen(QColor(0, 0, 0, 30), 2))
        painter.drawRect(QRectF(0, 0, frame.width, frame.height))
        if frame.dividers:
            painter.setPen(QPen(QColor(0, 0, 0, 60), 2, Qt.DashLine))
            for (x0, y0), (x1, y1) in frame.dividers:
                painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))

    def _paint_highlights(self, painter: QPainter, model: PageModel) -> None:
        """Highlights: translucent bands over the base document's own text."""
        if not model.highlights:
            return
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setCompositionMode(QPainter.CompositionMode_Multiply)
        for hl in model.highlights:
            if not hl.layer_visible:
                continue
            color = QColor(hl.color)
            color.setAlphaF(hl.alpha)
            painter.setBrush(color)
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
            if not block.layer_visible or not block.text.strip():
                continue
            # a block normally arrives laid out; one that does not is wrapped at
            # its own width, as the decoder and the PDF export wrap it
            lines = block.lines or lay_out_lines(block.paragraphs or [("PLAIN", block.text)], block.width)
            for ln, st, number in numbered(lines):
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
                    self._paint_marker(painter, st.marker, x, y, number)
        painter.restore()

    @staticmethod
    def _paint_marker(painter: QPainter, marker: str, x: float, baseline: float, number: int) -> None:
        label = marker_label(marker, number)
        if marker in ("bullet", "bullet2"):
            painter.drawText(QPointF(x - MARKER_GAP, baseline), label)
        elif marker in ("number", "letter"):
            width = painter.fontMetrics().horizontalAdvance(label)
            painter.drawText(QPointF(x - MARKER_GAP + NUMBER_MARKER_NUDGE - width, baseline), label)
        elif marker in ("box", "box_checked"):
            top = baseline - CHECKBOX_SIZE
            rect = QRectF(x - CHECKBOX_GAP, top, CHECKBOX_SIZE, CHECKBOX_SIZE)
            painter.save()
            painter.setPen(QPen(QColor("#1a1a1a"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
            if marker == "box_checked":
                (ax, ay), (bx, by), (cx, cy) = CHECK_MARK
                start = QPointF(rect.left() + ax, rect.top() + ay)
                low = QPointF(rect.left() + bx, rect.bottom() + by)
                end = QPointF(rect.right() + cx, rect.top() + cy)
                painter.drawLine(start, low)
                painter.drawLine(low, end)
            painter.restore()
