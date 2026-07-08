from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QWheelEvent
from PySide6.QtWidgets import QLabel, QScrollArea


class ZoomableImageView(QScrollArea):
    zoom_changed = Signal(float)

    def __init__(self) -> None:
        super().__init__()
        self._pixmap = QPixmap()
        self._zoom_factor = 1.0
        self._message = "无法加载图片预览"

        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setObjectName("ZoomableImageView")

        self.image_label = QLabel()
        self.image_label.setObjectName("PreviewImage")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setText(self._message)
        self.setWidget(self.image_label)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._zoom_factor = 1.0
        self._apply_pixmap()

    def show_message(self, message: str) -> None:
        self._pixmap = QPixmap()
        self._zoom_factor = 1.0
        self._message = message
        self._apply_pixmap()

    def reset_zoom(self) -> None:
        if self._pixmap.isNull():
            return
        self._zoom_factor = 1.0
        self._apply_pixmap()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._pixmap.isNull():
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        step = 1.15 if delta > 0 else 1 / 1.15
        self._zoom_factor = max(0.2, min(8.0, self._zoom_factor * step))
        self._apply_pixmap()
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._pixmap.isNull():
            self._apply_pixmap()

    def _fit_scale(self) -> float:
        if self._pixmap.isNull():
            return 1.0

        viewport_size = self.viewport().size()
        if viewport_size.width() <= 0 or viewport_size.height() <= 0:
            return 1.0

        width_scale = viewport_size.width() / self._pixmap.width()
        height_scale = viewport_size.height() / self._pixmap.height()
        return min(width_scale, height_scale, 1.0)

    def _apply_pixmap(self) -> None:
        if self._pixmap.isNull():
            self.image_label.clear()
            self.image_label.setText(self._message)
            self.zoom_changed.emit(1.0)
            return

        final_scale = self._fit_scale() * self._zoom_factor
        final_scale = max(0.05, final_scale)
        scaled = self._pixmap.scaled(
            max(1, int(self._pixmap.width() * final_scale)),
            max(1, int(self._pixmap.height() * final_scale)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)
        self.image_label.resize(scaled.size())
        self.zoom_changed.emit(self._zoom_factor)
