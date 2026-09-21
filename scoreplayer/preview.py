from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


class ScoreImagePreview(QWidget):
    """Page preview with a measure-level playback overlay."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._source_path: str | None = None
        self._highlight: tuple[int, int, int, int] | None = None
        self._highlight_label = ""
        self._empty_text = "添加曲谱图片后会显示在这里"
        self.setMinimumSize(580, 620)

    @property
    def source_path(self) -> str | None:
        return self._source_path

    def set_source(self, path: str | Path):
        self._source_path = str(path)
        self._pixmap = QPixmap(self._source_path)
        if self._pixmap.isNull():
            self._empty_text = "图片无法打开"
        self.update()

    def clear_source(self):
        self._source_path = None
        self._pixmap = QPixmap()
        self._highlight = None
        self._highlight_label = ""
        self.update()

    def set_empty_text(self, text: str):
        self._empty_text = text
        self.update()

    def set_highlight(
        self,
        rect: tuple[int, int, int, int] | None,
        label: str = "",
    ):
        self._highlight = rect
        self._highlight_label = label
        self.update()

    def clear_highlight(self):
        self.set_highlight(None, "")

    def _draw_rect(self) -> QRectF | None:
        if self._pixmap.isNull():
            return None

        source_w = self._pixmap.width()
        source_h = self._pixmap.height()
        if source_w <= 0 or source_h <= 0:
            return None

        margin = 12.0
        target_w = max(1.0, self.width() - 2 * margin)
        target_h = max(1.0, self.height() - 2 * margin)
        scale = min(target_w / source_w, target_h / source_h)

        draw_w = source_w * scale
        draw_h = source_h * scale
        left = (self.width() - draw_w) / 2.0
        top = (self.height() - draw_h) / 2.0
        return QRectF(left, top, draw_w, draw_h)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor("#f5f6f8"))

        draw_rect = self._draw_rect()
        if draw_rect is None:
            painter.setPen(QColor("#667085"))
            painter.drawText(self.rect(), Qt.AlignCenter, self._empty_text)
            return

        painter.drawPixmap(draw_rect, self._pixmap, QRectF(self._pixmap.rect()))

        if self._highlight is None:
            return

        x0, y0, x1, y1 = self._highlight
        sx = draw_rect.width() / self._pixmap.width()
        sy = draw_rect.height() / self._pixmap.height()

        overlay = QRectF(
            draw_rect.left() + x0 * sx,
            draw_rect.top() + y0 * sy,
            max(2.0, (x1 - x0) * sx),
            max(2.0, (y1 - y0) * sy),
        )

        painter.fillRect(overlay, QColor(20, 184, 166, 48))
        painter.setPen(QPen(QColor("#0f766e"), 2.5))
        painter.drawRoundedRect(overlay, 6, 6)

        if self._highlight_label:
            font = QFont()
            font.setBold(True)
            font.setPointSize(10)
            painter.setFont(font)

            text_rect = QRectF(
                overlay.left() + 6,
                max(draw_rect.top() + 4, overlay.top() - 28),
                min(260.0, max(130.0, overlay.width())),
                24,
            )
            painter.fillRect(text_rect, QColor(15, 118, 110, 225))
            painter.setPen(QColor("white"))
            painter.drawText(
                text_rect.adjusted(7, 0, -5, 0),
                Qt.AlignVCenter | Qt.AlignLeft,
                self._highlight_label,
            )
