"""Qt painting of reMarkable pages.

The painter draws a :class:`~remarksync.layout.PageFrame`: background,
base-document underlay or template, highlights, ink, typed text, then the
paper border and overflow dividers. It computes no geometry; every rect
and line comes from the frame.
"""

from __future__ import annotations

import fitz  # pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import QWidget

from .layout import PageFrame, lay_out
from .library import TEMPLATE_BLANK, TEMPLATE_DOTS, TEMPLATE_GRID, TEMPLATE_ISOMETRIC, TEMPLATE_RULED, DocItem, PageRef
from .rmdata import PageModel, decode_page
from .textmetrics import TEXT_FONT_PX, TEXT_MIN_BOX_WIDTH

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

    # -- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
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
            path = QPainterPath(QPointF(*stroke.points[0]))
            for x, y in stroke.points[1:]:
                path.lineTo(QPointF(x, y))
            color = QColor(stroke.color)
            pen = QPen(color)
            pen.setWidthF(max(0.4, stroke.width))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.save()
            if stroke.highlighter:
                color.setAlpha(110)
                pen.setColor(color)
            painter.setPen(pen)
            if stroke.highlighter:
                painter.setCompositionMode(QPainter.CompositionMode_Multiply)
            painter.drawPath(path)
            painter.restore()

    def _paint_texts(self, painter: QPainter, model: PageModel) -> None:
        if not model.texts:
            return
        painter.save()
        for block in model.texts:
            if not block.text.strip():
                continue
            box_width = max(block.width, TEXT_MIN_BOX_WIDTH)

            font = QFont("Segoe UI")
            font.setPixelSize(TEXT_FONT_PX)
            option = QTextOption()
            option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            layout = QTextLayout(block.text, font, painter.device())
            layout.setTextOption(option)
            layout.setCacheEnabled(True)
            line_y = block.pos_y
            layout.beginLayout()
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(box_width)
                line.setPosition(QPointF(block.pos_x, line_y))
                line.draw(painter, QPointF(0, 0))
                line_y += line.height()
            layout.endLayout()
        painter.restore()
