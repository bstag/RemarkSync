"""Qt rendering of reMarkable pages: background template, base-PDF underlay,
pen strokes and typed text onto a QWidget canvas.
"""

from __future__ import annotations

import fitz  # pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import QWidget

from .library import (TEMPLATE_BLANK, TEMPLATE_DOTS, TEMPLATE_GRID,
                      TEMPLATE_ISOMETRIC, TEMPLATE_RULED, DocItem, PageRef)
from .rmdata import PAGE_HEIGHT, PAGE_WIDTH, PageModel, parse_rm

TEXT_FONT_PT = 30  # rm units, tuned to look close to on-device text size

_TEMPLATE_PEN = QColor(0xB3, 0xB3, 0xB3)


def draw_template(painter: QPainter, template: str, rect: QRectF) -> None:
    """Paint the page background pattern (dots / ruled / grid / isometric)."""
    if template == TEMPLATE_BLANK:
        return
    painter.save()
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
    """Renders base-PDF pages to QImages sized to the tablet canvas."""

    def __init__(self, pdf_path):
        self.doc = fitz.open(pdf_path)
        self._cache: dict[int, QImage] = {}

    def page_image(self, index: int) -> QImage | None:
        if index in self._cache:
            return self._cache[index]
        if index >= self.doc.page_count:
            return None
        page = self.doc.load_page(index)
        # pdf pages can be rotated; fitz handles rotation in the matrix
        zoom = max(PAGE_WIDTH / page.rect.width, PAGE_HEIGHT / page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
        img = img.copy()  # detach from pymupdf buffer
        img = img.scaled(PAGE_WIDTH, PAGE_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._cache[index] = img
        return img

    def close(self) -> None:
        self.doc.close()


class PageCanvas(QWidget):
    """One reMarkable page. Fixed size = page size * scale; grows taller
    for endless-canvas journal pages once the model has been parsed."""

    def __init__(self, doc: DocItem, page: PageRef, underlay: PdfUnderlay | None, scale: float = 1.0):
        super().__init__()
        self._doc = doc
        self._page = page
        self._underlay = underlay
        self._pdf_mode = doc.file_type == "pdf"
        # parse eagerly so the fixed size is correct before the first paint
        # (resizing during/after paint is unreliable in Qt)
        try:
            self._model = (
                parse_rm(self._page.rm_path, pdf_mode=self._pdf_mode)
                if self._page.rm_path
                else PageModel()
            )
        except Exception:
            self._model = PageModel()
        self._scale = scale
        self.set_scale(scale)
        self.setToolTip(f"{doc.name} — page {page.index + 1}")

    def _page_height(self) -> float:
        return self._model.content_size[1]

    def _page_width(self) -> float:
        return self._model.content_size[0]

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        self.setFixedSize(int(self._page_width() * scale), int(self._page_height() * scale))
        self.update()

    def model(self) -> PageModel:
        return self._model

    # -- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(self._scale, self._scale)

        model = self.model()
        ox, _oy = model.origin
        rect = QRectF(0, 0, self._page_width(), self._page_height())
        painter.fillRect(rect, Qt.white)

        if self._underlay is not None and self._doc.file_type == "pdf":
            img = self._underlay.page_image(self._page.index)
            if img is not None:
                painter.drawImage(QRectF(ox, 0, PAGE_WIDTH, PAGE_HEIGHT), img)
            self._paint_highlights(painter, model)
        elif self._page.index == 0 or model.content_size[1] <= PAGE_HEIGHT:
            # only the first page of an endless canvas shows a template
            draw_template(painter, self._page.template, QRectF(ox, 0, PAGE_WIDTH, PAGE_HEIGHT))

        self._paint_strokes(painter, model)
        self._paint_texts(painter, model)

        painter.setPen(QPen(QColor(0, 0, 0, 30), 2))
        painter.drawRect(rect)
        if self._pdf_mode and (self._page_height() > PAGE_HEIGHT or ox > 0):
            # divider showing where the PDF page ends and overflow begins
            pen = QPen(QColor(0, 0, 0, 60), 2, Qt.DashLine)
            painter.setPen(pen)
            if self._page_height() > PAGE_HEIGHT:
                painter.drawLine(QPointF(0, PAGE_HEIGHT), QPointF(self._page_width(), PAGE_HEIGHT))
            if ox > 0:
                painter.drawLine(QPointF(ox, 0), QPointF(ox, PAGE_HEIGHT))
                painter.drawLine(QPointF(ox + PAGE_WIDTH, 0), QPointF(ox + PAGE_WIDTH, PAGE_HEIGHT))

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
        from PySide6.QtGui import QTextLayout, QTextOption

        for block in model.texts:
            if not block.text.strip():
                continue
            box = QRectF(block.pos_x, block.pos_y, max(block.width, 50.0), block.height + 20)

            font = QFont("Segoe UI")
            font.setPixelSize(TEXT_FONT_PT)
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
                line.setLineWidth(box.width())
                line.setPosition(QPointF(block.pos_x, line_y))
                line.draw(painter, QPointF(0, 0))
                line_y += line.height()
            layout.endLayout()
        painter.restore()
