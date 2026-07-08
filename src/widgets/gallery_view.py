from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import subprocess

from PySide6.QtCore import QByteArray, QMimeData, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QMenu, QScrollArea, QVBoxLayout, QWidget

from src.models.media_item import MediaItem
from src.services.thumbnail_service import ThumbnailService
from src.widgets.flow_layout import FlowLayout
from src.widgets.loading_overlay import LoadingOverlay
from src.widgets.media_thumbnail_card import MediaThumbnailCard


class GalleryView(QScrollArea):
    BATCH_SIZE = 180
    MAX_WINDOW_ITEMS = BATCH_SIZE * 3
    SCROLL_PRELOAD_MARGIN = 360

    open_requested = Signal(str)
    favorite_toggled = Signal(str, bool)
    current_index_changed = Signal(int)
    selection_changed = Signal(int)
    status_message_requested = Signal(str)

    def __init__(self, thumbnail_service: ThumbnailService) -> None:
        super().__init__()
        self.thumbnail_service = thumbnail_service
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(4)
        self.thread_pool.setExpiryTimeout(15000)
        self.group_mode = "day"
        self.thumbnail_size = 180
        self._all_items: list[MediaItem] = []
        self._empty_text = "没有匹配到照片或视频"
        self._render_start = 0
        self._render_end = 0
        self._path_to_widget: dict[str, QWidget] = {}
        self._group_sections: OrderedDict[str, FlowLayout] = OrderedDict()
        self._loading_from_scroll = False
        self._last_emitted_index = -1
        self._selection_mode = False
        self._selected_paths: set[str] = set()
        self._selection_anchor_path: str | None = None
        self._pending_thumbnail_paths: set[str] = set()
        self._external_loading_message: str | None = None

        self.setObjectName("GalleryView")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setObjectName("GalleryViewport")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.container = QWidget()
        self.container.setObjectName("GalleryContainer")
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(8, 8, 8, 18)
        self.container_layout.setSpacing(18)
        self.setWidget(self.container)
        self.loading_overlay = LoadingOverlay(self.viewport(), message="正在加载预览...", dark=False)

        self.verticalScrollBar().valueChanged.connect(self._on_scroll_value_changed)

    @property
    def visible_count(self) -> int:
        return max(0, self._render_end - self._render_start)

    @property
    def total_count(self) -> int:
        return len(self._all_items)

    @property
    def render_start(self) -> int:
        return self._render_start

    @property
    def render_end(self) -> int:
        return self._render_end

    @property
    def selection_count(self) -> int:
        return len(self._selected_paths)

    def set_items(
        self,
        items: list[MediaItem],
        group_mode: str,
        thumbnail_size: int,
        empty_text: str = "没有匹配到照片或视频",
    ) -> None:
        self.group_mode = group_mode
        self.thumbnail_size = thumbnail_size
        self._all_items = items
        self._empty_text = empty_text
        valid_paths = {item.path for item in items}
        self._selected_paths &= valid_paths
        if self._selection_anchor_path not in valid_paths:
            self._selection_anchor_path = None
        if not self._selected_paths:
            self._selection_mode = False
        self._emit_selection_changed()

        if not items:
            self._render_window(0, 0)
            return

        end = min(self.BATCH_SIZE, len(items))
        self._render_window(0, end, reset_scroll=True)

    def jump_to_index(self, index: int) -> None:
        if not self._all_items:
            return

        index = max(0, min(index, len(self._all_items) - 1))
        if index < self._render_start or index >= self._render_end:
            half_window = self.MAX_WINDOW_ITEMS // 2
            start = max(0, index - half_window)
            end = min(len(self._all_items), start + self.MAX_WINDOW_ITEMS)
            start = max(0, end - self.MAX_WINDOW_ITEMS)
            self._render_window(start, end, anchor_index=index, reset_scroll=True)
        else:
            while index >= self._render_end and self._render_end < len(self._all_items):
                self._append_batch()
            self._scroll_to_item(index)

    def current_global_index(self) -> int:
        if not self._all_items or self.visible_count <= 0:
            return 0

        scrollbar = self.verticalScrollBar()
        if scrollbar.maximum() <= 0:
            return self._render_start

        ratio = scrollbar.value() / max(1, scrollbar.maximum())
        local_index = int(ratio * max(0, self.visible_count - 1))
        return min(len(self._all_items) - 1, self._render_start + local_index)

    def selected_paths(self) -> list[str]:
        selected = []
        selected_set = self._selected_paths
        for item in self._all_items:
            if item.path in selected_set:
                selected.append(item.path)
        return selected

    def selected_items(self) -> list[MediaItem]:
        selected = []
        selected_set = self._selected_paths
        for item in self._all_items:
            if item.path in selected_set:
                selected.append(item)
        return selected

    def all_selected_are_favorite(self) -> bool:
        items = self.selected_items()
        return bool(items) and all(item.is_favorite for item in items)

    def set_rendered_favorite_state(self, media_path: str, value: bool) -> None:
        widget = self._path_to_widget.get(media_path)
        if isinstance(widget, MediaThumbnailCard):
            widget.set_favorite_checked(value)

    def clear_selection(self) -> None:
        if not self._selection_mode and not self._selected_paths:
            return
        self._selection_mode = False
        self._selected_paths.clear()
        self._selection_anchor_path = None
        self._sync_rendered_selection_state()
        self._emit_selection_changed()

    def set_loading_state(self, message: str | None) -> None:
        self._external_loading_message = message
        self._update_loading_overlay()

    def copy_selected_to_clipboard(self) -> int:
        paths = self.selected_paths()
        if not paths:
            return 0

        self.setFocus()
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(path) for path in paths])
        drop_effect = QByteArray((1).to_bytes(4, "little"))
        mime_data.setData('application/x-qt-windows-mime;value="Preferred DropEffect"', drop_effect)
        mime_data.setData("Preferred DropEffect", drop_effect)
        QGuiApplication.clipboard().setMimeData(mime_data)
        self.status_message_requested.emit(f"已复制 {len(paths)} 个项目，可直接粘贴到资源管理器。")
        return len(paths)

    def _render_window(
        self,
        start: int,
        end: int,
        *,
        anchor_index: int | None = None,
        reset_scroll: bool = False,
    ) -> None:
        self._clear_layout()
        self._render_start = start
        self._render_end = start
        self._pending_thumbnail_paths.clear()

        if start >= end or not self._all_items:
            self.loading_overlay.hide_overlay()
            empty_label = QLabel(self._empty_text)
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setObjectName("EmptyLabel")
            self.container_layout.addWidget(empty_label)
            self.container_layout.addStretch(1)
            self._emit_current_index(0)
            return

        self._append_range(start, end)
        self._update_loading_overlay()

        QApplication.processEvents()
        if anchor_index is not None:
            self._scroll_to_item(anchor_index)
        elif reset_scroll:
            self.verticalScrollBar().setValue(0)
            self._emit_current_index(self._render_start)

    def _append_batch(self) -> None:
        if self._render_end >= len(self._all_items):
            return
        anchor_index = self.current_global_index()
        next_end = min(len(self._all_items), self._render_end + self.BATCH_SIZE)
        self._append_range(self._render_end, next_end)
        self._trim_window(anchor_index)

    def _prepend_batch(self) -> None:
        if self._render_start <= 0:
            return

        anchor_index = self.current_global_index()
        new_start = max(0, self._render_start - self.BATCH_SIZE)
        self._render_window(new_start, self._render_end, anchor_index=anchor_index, reset_scroll=True)
        self._trim_window(anchor_index)

    def _append_range(self, start: int, end: int) -> None:
        for item in self._all_items[start:end]:
            group_key = self._group_key_for(item)
            flow_layout = self._group_sections.get(group_key)
            if flow_layout is None:
                header = QLabel(group_key)
                header.setObjectName("SectionTitle")
                self.container_layout.addWidget(header)

                section = QWidget()
                section.setObjectName("GallerySection")
                flow_layout = FlowLayout(section, margin=0, spacing=14)
                self._group_sections[group_key] = flow_layout
                self.container_layout.addWidget(section)

            card = MediaThumbnailCard(item, self.thumbnail_service, self.thumbnail_size)
            card.activated.connect(self.open_requested)
            card.favorite_toggled.connect(self.favorite_toggled)
            card.thumbnail_requested.connect(self._start_thumbnail_task)
            card.thumbnail_finished.connect(self._on_thumbnail_finished)
            card.selection_toggled.connect(self._on_card_selection_toggled)
            card.selection_mode_requested.connect(self._on_card_selection_mode_requested)
            card.context_menu_requested.connect(self._open_card_context_menu)
            card.set_selection_mode(self._selection_mode)
            card.set_selected(item.path in self._selected_paths)
            flow_layout.addWidget(card)
            self._path_to_widget[item.path] = card
            self._pending_thumbnail_paths.add(item.path)
            card.request_thumbnail()

        self._render_end = end
        self._emit_current_index(self.current_global_index())

    def _group_key_for(self, item: MediaItem) -> str:
        if self.group_mode == "year":
            return item.capture_time.strftime("%Y年")
        if self.group_mode == "month":
            return item.capture_time.strftime("%Y年%m月")
        return item.capture_time.strftime("%Y年%m月%d日")

    def _scroll_to_item(self, index: int) -> None:
        index = max(0, min(index, len(self._all_items) - 1))
        widget = self._path_to_widget.get(self._all_items[index].path)
        if widget is None:
            return
        self.ensureWidgetVisible(widget, 0, 80)
        self._emit_current_index(index)

    def _start_thumbnail_task(self, task) -> None:
        self.thread_pool.start(task)

    def _on_scroll_value_changed(self, _value: int) -> None:
        if self._loading_from_scroll or not self._all_items:
            return

        self._emit_current_index(self.current_global_index())

        scrollbar = self.verticalScrollBar()
        if scrollbar.maximum() <= 0:
            return

        self._loading_from_scroll = True
        try:
            if scrollbar.maximum() - scrollbar.value() <= self.SCROLL_PRELOAD_MARGIN:
                self._append_batch()
            elif scrollbar.value() <= self.SCROLL_PRELOAD_MARGIN and self._render_start > 0:
                self._prepend_batch()
        finally:
            self._loading_from_scroll = False

    def _emit_current_index(self, index: int) -> None:
        if index == self._last_emitted_index:
            return
        self._last_emitted_index = index
        self.current_index_changed.emit(index)

    def _emit_selection_changed(self) -> None:
        self.selection_changed.emit(len(self._selected_paths))

    def _trim_window(self, anchor_index: int | None = None) -> None:
        if self.visible_count <= self.MAX_WINDOW_ITEMS or not self._all_items:
            return

        anchor = self.current_global_index() if anchor_index is None else anchor_index
        half_window = self.MAX_WINDOW_ITEMS // 2
        start = max(0, anchor - half_window)
        end = min(len(self._all_items), start + self.MAX_WINDOW_ITEMS)
        start = max(0, end - self.MAX_WINDOW_ITEMS)
        if start == self._render_start and end == self._render_end:
            return
        self._render_window(start, end, anchor_index=anchor, reset_scroll=True)

    def _sync_rendered_selection_state(self) -> None:
        for path, widget in self._path_to_widget.items():
            if isinstance(widget, MediaThumbnailCard):
                widget.set_selection_mode(self._selection_mode)
                widget.set_selected(path in self._selected_paths)

    def _on_card_selection_mode_requested(self, media_path: str) -> None:
        self.setFocus()
        self._selection_mode = True
        self._selected_paths = {media_path}
        self._selection_anchor_path = media_path
        self._sync_rendered_selection_state()
        self._emit_selection_changed()

    def _on_card_selection_toggled(self, media_path: str, selected: bool) -> None:
        self.setFocus()
        modifiers = QGuiApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ShiftModifier and self._selection_anchor_path:
            self._selection_mode = True
            self._select_range(media_path)
            self._sync_rendered_selection_state()
            self._emit_selection_changed()
            return

        if selected:
            self._selection_mode = True
            self._selected_paths.add(media_path)
            self._selection_anchor_path = media_path
        else:
            self._selected_paths.discard(media_path)
            if not self._selected_paths:
                self._selection_mode = False
                self._selection_anchor_path = None
            elif self._selection_anchor_path == media_path:
                self._selection_anchor_path = next(iter(self._selected_paths), None)
        self._sync_rendered_selection_state()
        self._emit_selection_changed()

    def _on_thumbnail_finished(self, media_path: str, _success: bool) -> None:
        self._pending_thumbnail_paths.discard(media_path)
        self._update_loading_overlay()

    def _update_loading_overlay(self) -> None:
        if self._external_loading_message:
            self.loading_overlay.show_message(self._external_loading_message, block_input=False)
            return
        if self._pending_thumbnail_paths:
            self.loading_overlay.show_message("正在加载预览...", block_input=False)
            return
        self.loading_overlay.hide_overlay()

    def _item_by_path(self, media_path: str) -> MediaItem | None:
        return next((item for item in self._all_items if item.path == media_path), None)

    def _select_range(self, media_path: str) -> None:
        if not self._all_items:
            return
        anchor_path = self._selection_anchor_path or media_path
        start_index = self._index_for_path(anchor_path)
        end_index = self._index_for_path(media_path)
        if start_index < 0 or end_index < 0:
            self._selected_paths.add(media_path)
            self._selection_anchor_path = media_path
            return

        low = min(start_index, end_index)
        high = max(start_index, end_index)
        for item in self._all_items[low : high + 1]:
            self._selected_paths.add(item.path)
        self._selection_anchor_path = media_path

    def _index_for_path(self, media_path: str) -> int:
        for index, item in enumerate(self._all_items):
            if item.path == media_path:
                return index
        return -1

    def _open_card_context_menu(self, media_path: str, global_pos) -> None:
        self.setFocus()
        if media_path not in self._selected_paths:
            self._selection_mode = True
            self._selected_paths = {media_path}
            self._selection_anchor_path = media_path
            self._sync_rendered_selection_state()
            self._emit_selection_changed()

        item = self._item_by_path(media_path)
        if item is None:
            return

        selected_paths = self.selected_paths()
        single_selection = len(selected_paths) == 1
        menu = QMenu(self)

        open_action = menu.addAction("打开")
        open_action.setEnabled(single_selection)

        copy_action = menu.addAction("复制")
        copy_action.setEnabled(bool(selected_paths))

        reveal_action = menu.addAction("在资源管理器中显示")
        reveal_action.setEnabled(single_selection)

        favorite_text = "取消收藏" if item.is_favorite else "收藏"
        favorite_action = menu.addAction(favorite_text)
        favorite_action.setEnabled(single_selection)

        select_all_action = menu.addAction("全选当前结果")
        clear_action = menu.addAction("清除选择")
        clear_action.setEnabled(self._selection_mode)

        chosen = menu.exec(global_pos)
        if chosen is open_action and single_selection:
            self.open_requested.emit(selected_paths[0])
        elif chosen is copy_action:
            self.copy_selected_to_clipboard()
        elif chosen is reveal_action and single_selection:
            self._reveal_in_explorer(selected_paths[0])
        elif chosen is favorite_action and single_selection:
            new_value = not item.is_favorite
            item.is_favorite = new_value
            widget = self._path_to_widget.get(item.path)
            if isinstance(widget, MediaThumbnailCard):
                widget.set_favorite_checked(new_value)
            self.favorite_toggled.emit(item.path, new_value)
        elif chosen is select_all_action:
            self._selection_mode = True
            self._selected_paths = {item.path for item in self._all_items}
            self._selection_anchor_path = self._all_items[0].path if self._all_items else None
            self._sync_rendered_selection_state()
            self._emit_selection_changed()
        elif chosen is clear_action:
            self.clear_selection()

    def _reveal_in_explorer(self, media_path: str) -> None:
        path = Path(media_path)
        try:
            subprocess.Popen(["explorer", "/select,", str(path)])
            self.status_message_requested.emit("已在资源管理器中定位文件。")
        except OSError:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
            self.status_message_requested.emit("已打开所在文件夹。")

    def _clear_layout(self) -> None:
        self._path_to_widget.clear()
        self._group_sections.clear()
        self._last_emitted_index = -1

        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            widget = item.widget()
            layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif layout is not None:
                self._delete_layout(layout)

    def _delete_layout(self, layout) -> None:
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                self._delete_layout(child.layout())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            if self.copy_selected_to_clipboard():
                event.accept()
                return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            if self._all_items:
                self._selection_mode = True
                self._selected_paths = {item.path for item in self._all_items}
                self._selection_anchor_path = self._all_items[0].path
                self._sync_rendered_selection_state()
                self._emit_selection_changed()
                event.accept()
                return
        if event.key() == Qt.Key.Key_Escape and self._selection_mode:
            self.clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)
