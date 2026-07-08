from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QTimer, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QLabel, QToolButton, QVBoxLayout

from src.models.media_item import MediaItem
from src.services.thumbnail_service import ThumbnailService


LOGGER = logging.getLogger(__name__)


class ThumbnailTaskSignals(QObject):
    finished = Signal(str, int)


class ThumbnailTask(QRunnable):
    def __init__(self, service: ThumbnailService, item: MediaItem, size: int) -> None:
        super().__init__()
        self.service = service
        self.item = item
        self.size = size
        self.signals = ThumbnailTaskSignals()

    def run(self) -> None:
        try:
            output = self.service.ensure_thumbnail(self.item, self.size)
            try:
                self.signals.finished.emit(str(output), self.size)
            except RuntimeError:
                return
        except Exception as exc:  # pragma: no cover - background safety
            LOGGER.warning("Thumbnail task failed for %s: %s", self.item.path, exc)


class MediaThumbnailCard(QFrame):
    activated = Signal(str)
    favorite_toggled = Signal(str, bool)
    thumbnail_requested = Signal(object)
    thumbnail_finished = Signal(str, bool)
    selection_toggled = Signal(str, bool)
    selection_mode_requested = Signal(str)
    context_menu_requested = Signal(str, object)

    def __init__(self, item: MediaItem, thumbnail_service: ThumbnailService, thumbnail_size: int) -> None:
        super().__init__()
        self.item = item
        self.thumbnail_service = thumbnail_service
        self.thumbnail_size = thumbnail_size
        self._thumbnail_task: ThumbnailTask | None = None
        self._selection_mode = False
        self._selected = False
        self._syncing_checkbox = False
        self._press_pos = None
        self._long_press_triggered = False

        self.setObjectName("MediaCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 12)
        layout.setSpacing(10)

        self.image_frame = QFrame()
        self.image_frame.setObjectName("ThumbFrame")
        self.image_frame.setFixedSize(thumbnail_size, thumbnail_size)
        layout.addWidget(self.image_frame, alignment=Qt.AlignmentFlag.AlignCenter)

        self.image_label = QLabel(self.image_frame)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setText("加载中...")
        self.image_label.setObjectName("ThumbImage")
        self.image_label.setGeometry(0, 0, thumbnail_size, thumbnail_size)

        self.select_checkbox = QCheckBox(self.image_frame)
        self.select_checkbox.setObjectName("SelectCheckbox")
        self.select_checkbox.show()
        self.select_checkbox.stateChanged.connect(self._on_checkbox_state_changed)

        self.badge_label = QLabel(self.image_frame)
        self.badge_label.setObjectName("ThumbBadge")
        self.badge_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.favorite_button = QToolButton(self.image_frame)
        self.favorite_button.setCheckable(True)
        self.favorite_button.setChecked(self.item.is_favorite)
        self.favorite_button.clicked.connect(self._on_favorite_clicked)
        self.favorite_button.setObjectName("FavoriteButton")
        self._update_favorite_button_text()

        self.name_label = QLabel(self.item.filename)
        self.name_label.setObjectName("CardTitle")
        self.name_label.setWordWrap(False)
        self.name_label.setToolTip(self.item.filename)
        layout.addWidget(self.name_label)

        self.meta_label = QLabel(self.item.capture_time.strftime("%Y-%m-%d %H:%M"))
        self.meta_label.setObjectName("CardMeta")
        layout.addWidget(self.meta_label)

        self.long_press_timer = QTimer(self)
        self.long_press_timer.setSingleShot(True)
        self.long_press_timer.setInterval(360)
        self.long_press_timer.timeout.connect(self._on_long_press_timeout)

        self._apply_badge()
        self.set_thumbnail_size(thumbnail_size)

    def request_thumbnail(self) -> None:
        cached_thumbnail = self.thumbnail_service.find_existing_thumbnail(self.item, self.thumbnail_size)
        if cached_thumbnail is not None:
            success = self._apply_thumbnail(str(cached_thumbnail), self.thumbnail_size)
            self.thumbnail_finished.emit(self.item.path, success)
            return

        task = ThumbnailTask(self.thumbnail_service, self.item, self.thumbnail_size)
        task.signals.finished.connect(self._on_thumbnail_ready)
        self._thumbnail_task = task
        self.thumbnail_requested.emit(task)

    def set_thumbnail_size(self, size: int) -> None:
        self.thumbnail_size = size
        self.setFixedWidth(size + 24)
        self.image_frame.setFixedSize(size, size)
        self.image_label.setGeometry(0, 0, size, size)
        self._update_overlay_positions()
        self._update_name_label()

    def set_selection_mode(self, enabled: bool) -> None:
        self._selection_mode = enabled
        if not enabled:
            self.set_selected(False)
        self._refresh_selection_style()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._syncing_checkbox = True
        self.select_checkbox.setChecked(selected)
        self._syncing_checkbox = False
        self._refresh_selection_style()

    def is_selected(self) -> bool:
        return self._selected

    def set_favorite_checked(self, checked: bool) -> None:
        self.favorite_button.setChecked(checked)
        self.item.is_favorite = checked
        self._update_favorite_button_text()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._clicked_overlay_control(event):
            self._press_pos = event.position().toPoint()
            self._long_press_triggered = False
            if not self._selection_mode:
                self.long_press_timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.long_press_timer.stop()
            if self._long_press_triggered:
                self._long_press_triggered = False
                event.accept()
                return
            if self._clicked_overlay_control(event):
                super().mouseReleaseEvent(event)
                return
            if self._selection_mode:
                self.selection_toggled.emit(self.item.path, not self._selected)
                event.accept()
                return
            self.activated.emit(self.item.path)
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.long_press_timer.isActive() and self._press_pos is not None:
            if (event.position().toPoint() - self._press_pos).manhattanLength() > QApplication.startDragDistance():
                self.long_press_timer.stop()
        super().mouseMoveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_overlay_positions()
        self._update_name_label()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.context_menu_requested.emit(self.item.path, event.globalPos())
        event.accept()

    def _clicked_overlay_control(self, event: QMouseEvent) -> bool:
        child = self.childAt(event.position().toPoint())
        return self._matches_child(child, self.favorite_button) or self._matches_child(child, self.select_checkbox)

    @staticmethod
    def _matches_child(child, target) -> bool:
        return child is target or (
            child is not None and target.isAncestorOf(child)
        )

    def _apply_badge(self) -> None:
        if self.item.is_motion_photo:
            self.badge_label.setText("LIVE")
            self.badge_label.show()
        elif self.item.media_type == "video":
            self.badge_label.setText("VIDEO")
            self.badge_label.show()
        else:
            self.badge_label.hide()

    def _update_overlay_positions(self) -> None:
        self.select_checkbox.adjustSize()
        self.select_checkbox.move(12, 12)
        if self.badge_label.isVisible():
            self.badge_label.adjustSize()
            self.badge_label.move(12, self.image_frame.height() - self.badge_label.height() - 12)
        self.favorite_button.adjustSize()
        self.favorite_button.move(self.image_frame.width() - self.favorite_button.width() - 12, 12)

    def _update_name_label(self) -> None:
        metrics = self.name_label.fontMetrics()
        self.name_label.setText(
            metrics.elidedText(self.item.filename, Qt.TextElideMode.ElideMiddle, max(110, self.width() - 20))
        )

    def _on_thumbnail_ready(self, thumb_path: str, size: int) -> None:
        self._thumbnail_task = None
        if size != self.thumbnail_size or not Path(thumb_path).exists():
            self.thumbnail_finished.emit(self.item.path, False)
            return

        if self._apply_thumbnail(thumb_path, size):
            self.thumbnail_finished.emit(self.item.path, True)
            return
        self.thumbnail_finished.emit(self.item.path, False)

    def _apply_thumbnail(self, thumb_path: str, size: int) -> bool:
        pixmap = self.thumbnail_service.load_thumbnail_pixmap(thumb_path, size)
        if pixmap.isNull():
            self.image_label.setText("预览失败")
            return False

        scaled = pixmap.scaled(
            self.thumbnail_size,
            self.thumbnail_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)
        self.image_label.setText("")
        return True

    def _on_favorite_clicked(self, checked: bool) -> None:
        self.set_favorite_checked(checked)
        self.favorite_toggled.emit(self.item.path, checked)

    def _on_checkbox_state_changed(self, state: int) -> None:
        if self._syncing_checkbox:
            return
        self.selection_toggled.emit(self.item.path, state == Qt.CheckState.Checked.value)

    def _on_long_press_timeout(self) -> None:
        self._long_press_triggered = True
        self.selection_mode_requested.emit(self.item.path)

    def _refresh_selection_style(self) -> None:
        self.setProperty("selected", self._selected)
        self.image_frame.setProperty("selected", self._selected)
        self.select_checkbox.setProperty("selected", self._selected)
        for widget in (self, self.image_frame, self.select_checkbox):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def _update_favorite_button_text(self) -> None:
        is_checked = self.favorite_button.isChecked()
        self.favorite_button.setText("★" if is_checked else "☆")
        self.favorite_button.setToolTip("取消收藏" if is_checked else "收藏")
