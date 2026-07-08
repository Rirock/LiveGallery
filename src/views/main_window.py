from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.app_paths import CACHE_DIR, DATABASE_PATH, DEFAULT_SCAN_DIR, LOGO_PATH
from src.models.media_item import MediaItem
from src.services.database_service import DatabaseService
from src.services.media_scanner import MediaScanner
from src.services.thumbnail_service import ThumbnailService
from src.views.preview_dialog import PreviewDialog
from src.views.settings_view import SettingsView
from src.widgets.gallery_view import GalleryView
from src.widgets.time_axis_widget import TimeAxisWidget


LOGGER = logging.getLogger(__name__)


class ScanWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, scanner: MediaScanner, folder: str) -> None:
        super().__init__()
        self.scanner = scanner
        self.folder = folder

    @Slot()
    def run(self) -> None:
        try:
            result = self.scanner.scan_folder(self.folder, self.progress.emit)
        except Exception as exc:  # pragma: no cover - UI worker safety
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


class MainWindow(QMainWindow):
    LIVE_REFRESH_FIRST_BATCH = 120
    LIVE_REFRESH_INTERVAL = 5000
    LIVE_REFRESH_MIN_SECONDS = 1.2

    FILTERS = (
        ("all", "所有照片"),
        ("motion", "动态照片"),
        ("videos", "视频"),
        ("favorites", "收藏"),
        ("settings", "设置"),
    )

    def __init__(
        self,
        database_service: DatabaseService,
        scanner: MediaScanner,
        thumbnail_service: ThumbnailService,
    ) -> None:
        super().__init__()
        self.database_service = database_service
        self.scanner = scanner
        self.thumbnail_service = thumbnail_service
        self.motion_photo_service = scanner.metadata_service.motion_photo_service
        self.current_filter = "all"
        self.current_folder: str | None = None
        self.current_items: list[MediaItem] = []
        self.scan_thread: QThread | None = None
        self.scan_worker: ScanWorker | None = None
        self.scan_in_progress = False
        self.last_live_refresh_count = 0
        self.last_live_refresh_at = 0.0

        self.setWindowTitle("LiveGallery")
        if LOGO_PATH.exists():
            self.setWindowIcon(QIcon(str(LOGO_PATH)))
        self.resize(1520, 940)
        self._build_ui()
        self._apply_styles()
        self._restore_initial_folder()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(20)
        self.setCentralWidget(root)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(236)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(22, 22, 22, 22)
        sidebar_layout.setSpacing(10)

        if LOGO_PATH.exists():
            logo_label = QLabel()
            logo_label.setObjectName("AppLogo")
            logo_label.setPixmap(
                QPixmap(str(LOGO_PATH)).scaled(
                    42,
                    42,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            sidebar_layout.addWidget(logo_label, alignment=Qt.AlignmentFlag.AlignLeft)

        title = QLabel("LiveGallery")
        title.setObjectName("AppTitle")
        sidebar_layout.addWidget(title)

        subtitle = QLabel("本地照片、动态照片与视频")
        subtitle.setObjectName("AppSubtitle")
        subtitle.setWordWrap(True)
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addSpacing(8)

        self.filter_buttons: dict[str, QPushButton] = {}
        for key, label in self.FILTERS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("SidebarButton")
            button.clicked.connect(lambda checked=False, value=key: self._set_filter(value))
            sidebar_layout.addWidget(button)
            self.filter_buttons[key] = button
        self.filter_buttons["all"].setChecked(True)
        sidebar_layout.addStretch(1)
        root_layout.addWidget(sidebar)

        content = QFrame()
        content.setObjectName("ContentPanel")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(16)
        root_layout.addWidget(content, stretch=1)

        self.selection_bar = QFrame()
        self.selection_bar.setObjectName("SelectionBar")
        selection_bar_layout = QHBoxLayout(self.selection_bar)
        selection_bar_layout.setContentsMargins(18, 14, 18, 14)
        selection_bar_layout.setSpacing(12)

        self.selection_close_button = QPushButton("×")
        self.selection_close_button.setObjectName("SelectionCloseButton")
        self.selection_close_button.clicked.connect(lambda: self.gallery_view.clear_selection())
        selection_bar_layout.addWidget(self.selection_close_button)

        self.selection_title_label = QLabel("已选择 0 项")
        self.selection_title_label.setObjectName("SelectionTitle")
        selection_bar_layout.addWidget(self.selection_title_label)

        self.selection_hint_label = QLabel("可复制到资源管理器或批量收藏")
        self.selection_hint_label.setObjectName("SelectionHint")
        selection_bar_layout.addWidget(self.selection_hint_label)
        selection_bar_layout.addStretch(1)

        self.selection_copy_button = QPushButton("复制")
        self.selection_copy_button.setObjectName("SelectionActionButton")
        self.selection_copy_button.clicked.connect(self._copy_selected_items)
        selection_bar_layout.addWidget(self.selection_copy_button)

        self.selection_favorite_button = QPushButton("收藏")
        self.selection_favorite_button.setObjectName("SelectionActionButton")
        self.selection_favorite_button.clicked.connect(self._toggle_selection_favorite)
        selection_bar_layout.addWidget(self.selection_favorite_button)

        self.selection_clear_button = QPushButton("取消选择")
        self.selection_clear_button.setObjectName("SelectionGhostButton")
        self.selection_clear_button.clicked.connect(lambda: self.gallery_view.clear_selection())
        selection_bar_layout.addWidget(self.selection_clear_button)

        self.selection_bar.hide()
        content_layout.addWidget(self.selection_bar)

        toolbar_panel = QFrame()
        self.toolbar_panel = toolbar_panel
        toolbar_panel.setObjectName("ToolbarPanel")
        toolbar_panel_layout = QVBoxLayout(toolbar_panel)
        toolbar_panel_layout.setContentsMargins(18, 18, 18, 18)
        toolbar_panel_layout.setSpacing(14)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(10)
        toolbar_panel_layout.addLayout(toolbar_layout)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索文件名")
        self.search_edit.textChanged.connect(self.refresh_gallery)
        toolbar_layout.addWidget(self.search_edit, stretch=1)

        self.group_combo = QComboBox()
        self.group_combo.addItem("按天", "day")
        self.group_combo.addItem("按月", "month")
        self.group_combo.addItem("按年", "year")
        self.group_combo.currentIndexChanged.connect(self.refresh_gallery)
        self.group_combo.setFixedWidth(120)
        toolbar_layout.addWidget(self.group_combo)

        self.slider_label = QLabel("缩略图")
        self.slider_label.setObjectName("ToolbarLabel")
        toolbar_layout.addWidget(self.slider_label)

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(120, 260)
        self.size_slider.setValue(180)
        self.size_slider.valueChanged.connect(self.refresh_gallery)
        self.size_slider.setFixedWidth(140)
        toolbar_layout.addWidget(self.size_slider)

        path_row = QHBoxLayout()
        path_row.setSpacing(10)

        path_caption = QLabel("当前目录")
        path_caption.setObjectName("PathCaption")
        path_row.addWidget(path_caption)

        self.folder_label = QLabel("尚未选择文件夹")
        self.folder_label.setObjectName("FolderPath")
        self.folder_label.setWordWrap(True)
        path_row.addWidget(self.folder_label, stretch=1)
        toolbar_panel_layout.addLayout(path_row)
        content_layout.addWidget(toolbar_panel)

        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, stretch=1)

        self.gallery_page = QWidget()
        gallery_page_layout = QHBoxLayout(self.gallery_page)
        gallery_page_layout.setContentsMargins(0, 0, 0, 0)
        gallery_page_layout.setSpacing(18)

        self.gallery_view = GalleryView(self.thumbnail_service)
        self.gallery_view.open_requested.connect(self._open_preview)
        self.gallery_view.favorite_toggled.connect(self._set_favorite)
        self.gallery_view.current_index_changed.connect(self._sync_time_axis)
        self.gallery_view.selection_changed.connect(self._on_selection_changed)
        self.gallery_view.status_message_requested.connect(self.statusBar().showMessage)
        gallery_page_layout.addWidget(self.gallery_view, stretch=1)

        self.time_axis = TimeAxisWidget()
        self.time_axis.jump_requested.connect(self._jump_to_index)
        gallery_page_layout.addWidget(self.time_axis)

        self.stack.addWidget(self.gallery_page)

        self.settings_view = SettingsView()
        self.settings_view.choose_folder_requested.connect(self._choose_folder)
        self.settings_view.update_requested.connect(self._update_current_folder)
        self.settings_view.rebuild_requested.connect(self._rebuild_current_folder)
        self.stack.addWidget(self.settings_view)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedWidth(320)
        self.statusBar().addPermanentWidget(self.progress_bar)
        self.statusBar().showMessage("请选择照片目录开始使用")

        font = QFont("Segoe UI Variable Text", 10)
        if not font.exactMatch():
            font = QFont("Segoe UI", 10)
        self.setFont(font)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #f6f7f9;
            }
            QFrame#Sidebar, QFrame#ContentPanel, QFrame#TimeAxisPanel {
                background: #ffffff;
                border: 1px solid #e6e8ec;
                border-radius: 8px;
            }
            QFrame#ToolbarPanel {
                background: #ffffff;
                border: 1px solid #e6e8ec;
                border-radius: 8px;
            }
            QFrame#SelectionBar {
                background: #0f6cbd;
                border: none;
                border-radius: 8px;
            }
            QFrame#SettingsCard {
                background: #ffffff;
                border: 1px solid #e6e8ec;
                border-radius: 8px;
            }
            QLabel {
                color: #1f2328;
            }
            QLabel#AppTitle {
                font-size: 28px;
                font-weight: 700;
                color: #111827;
            }
            QLabel#AppSubtitle, QLabel#CardMeta, QLabel#EmptyLabel, QLabel#SettingsText, QLabel#LoadMoreInfo {
                color: #697586;
                font-size: 13px;
            }
            QLabel#SettingsKey {
                color: #4b5563;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#PathCaption {
                color: #697586;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel#FolderPath {
                color: #1f2328;
                font-size: 14px;
                font-weight: 600;
            }
            QLabel#ToolbarLabel {
                color: #4b5563;
                font-weight: 600;
            }
            QLabel#SectionTitle {
                font-size: 22px;
                font-weight: 700;
                color: #1f2328;
                padding-top: 6px;
            }
            QLabel#CardTitle {
                color: #1f2328;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#CardMeta {
                font-size: 12px;
                font-weight: 500;
            }
            QLabel#SettingsTitle {
                color: #111827;
                font-size: 24px;
                font-weight: 700;
            }
            QLabel#TimelineTitle {
                color: #1f2328;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#TimelineCurrentLabel {
                color: #1f2328;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#TimelineEdgeLabel {
                color: #697586;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#SelectionTitle {
                color: #ffffff;
                font-size: 18px;
                font-weight: 800;
            }
            QLabel#SelectionHint {
                color: rgba(255, 255, 255, 215);
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton#SidebarButton {
                text-align: left;
                padding: 11px 12px;
                border-radius: 8px;
                border: none;
                background: transparent;
                color: #1f2328;
                font-size: 15px;
                font-weight: 600;
                outline: none;
            }
            QPushButton#SidebarButton:hover {
                background: #f3f4f6;
            }
            QPushButton#SidebarButton:checked {
                background: #e8f2ff;
                color: #0f6cbd;
                border-left: 3px solid #0f6cbd;
            }
            QPushButton#SidebarButton:focus {
                outline: none;
            }
            QPushButton, QToolButton, QComboBox, QLineEdit {
                background: #ffffff;
                color: #1f2328;
                border: 1px solid #d8dce3;
                border-radius: 8px;
                padding: 9px 12px;
                font-weight: 600;
                outline: none;
            }
            QPushButton:hover, QToolButton:hover, QComboBox:hover, QLineEdit:hover {
                background: #f8fafc;
                border-color: #c7ccd4;
            }
            QPushButton:pressed, QToolButton:pressed {
                background: #eef2f6;
            }
            QPushButton#PrimaryButton {
                background: #0f6cbd;
                color: #ffffff;
                border-color: #0f6cbd;
            }
            QPushButton#PrimaryButton:hover {
                background: #115ea3;
                border-color: #115ea3;
            }
            QPushButton#SecondaryButton {
                background: #ffffff;
            }
            QPushButton#DangerButton {
                color: #b42318;
                background: #fff7f6;
                border-color: #f2c7c3;
            }
            QPushButton#SelectionCloseButton, QPushButton#SelectionActionButton, QPushButton#SelectionGhostButton {
                color: #ffffff;
                background: rgba(255, 255, 255, 0.12);
                border: 1px solid rgba(255, 255, 255, 0.2);
            }
            QPushButton#SelectionCloseButton {
                min-width: 42px;
                max-width: 42px;
                min-height: 42px;
                max-height: 42px;
                border-radius: 8px;
                padding: 0;
                font-size: 24px;
                font-weight: 700;
            }
            QPushButton#SelectionActionButton:hover, QPushButton#SelectionGhostButton:hover, QPushButton#SelectionCloseButton:hover {
                background: rgba(255, 255, 255, 0.18);
                border-color: rgba(255, 255, 255, 0.28);
            }
            QPushButton#SelectionActionButton:pressed, QPushButton#SelectionGhostButton:pressed, QPushButton#SelectionCloseButton:pressed {
                background: rgba(255, 255, 255, 0.22);
            }
            QPushButton#SelectionGhostButton {
                background: transparent;
            }
            QPushButton:disabled {
                color: #98a2b3;
                background: #f6f8fa;
            }
            QComboBox {
                padding-right: 28px;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #1f2328;
                border: 1px solid #d8dce3;
                selection-background-color: #eef2f6;
                selection-color: #1f2328;
            }
            QLineEdit::placeholder {
                color: #98a2b3;
            }
            QScrollArea#GalleryView, QWidget#GalleryViewport, QWidget#GalleryContainer, QWidget#GallerySection, QWidget#SettingsView {
                background: transparent;
                border: none;
            }
            QFrame#MediaCard {
                background: #ffffff;
                border: 1px solid #e6e8ec;
                border-radius: 8px;
            }
            QFrame#MediaCard[selected="true"] {
                background: #f3f8ff;
                border: 1px solid #0f6cbd;
            }
            QFrame#ThumbFrame {
                background: #f4f6f8;
                border: 1px solid #edf1f5;
                border-radius: 8px;
            }
            QFrame#ThumbFrame[selected="true"] {
                background: #e8f2ff;
                border: 1px solid #b7d8ff;
            }
            QLabel#ThumbImage {
                background: transparent;
                color: #7c8796;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 600;
            }
            QCheckBox#SelectCheckbox {
                background: rgba(255, 255, 255, 238);
                border: 1px solid #d8e0ea;
                border-radius: 11px;
                padding: 2px;
            }
            QCheckBox#SelectCheckbox::indicator {
                width: 18px;
                height: 18px;
            }
            QCheckBox#SelectCheckbox::indicator:unchecked {
                background: transparent;
                border: 2px solid #98a2b3;
                border-radius: 9px;
            }
            QCheckBox#SelectCheckbox::indicator:checked {
                background: #0f6cbd;
                border: 2px solid #0f6cbd;
                border-radius: 9px;
            }
            QLabel#ThumbBadge {
                background: rgba(31, 35, 40, 235);
                color: #ffffff;
                border-radius: 7px;
                padding: 4px 8px;
                font-size: 10px;
                font-weight: 800;
            }
            QToolButton#FavoriteButton {
                background: rgba(255, 255, 255, 238);
                color: #98a2b3;
                border: 1px solid #d8e0ea;
                border-radius: 8px;
                padding: 0;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
                font-size: 18px;
                font-weight: 700;
            }
            QToolButton#FavoriteButton:checked {
                background: rgba(255, 248, 222, 245);
                color: #f4c542;
                border-color: #f1d37a;
            }
            QMenu {
                background: #ffffff;
                color: #1f2328;
                border: 1px solid #e4e9f0;
                border-radius: 8px;
                padding: 8px;
            }
            QMenu::item {
                padding: 8px 14px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background: #f3f6fa;
            }
            QFrame#TimeAxisPanel {
                min-width: 110px;
            }
            QSlider#TimelineSlider::groove:vertical {
                width: 4px;
                background: #e2e8f0;
                border-radius: 2px;
            }
            QSlider#TimelineSlider::sub-page:vertical {
                background: #0f6cbd;
                border-radius: 2px;
            }
            QSlider#TimelineSlider::handle:vertical {
                height: 18px;
                margin: 0 -7px;
                background: #0f6cbd;
                border-radius: 9px;
            }
            QStatusBar {
                background: transparent;
                color: #697586;
            }
            QProgressBar {
                background: #eef2f6;
                color: #1f2328;
                border: 1px solid #d8dce3;
                border-radius: 8px;
                text-align: center;
                padding: 1px;
                font-weight: 700;
            }
            QProgressBar::chunk {
                background: #b9ddff;
                border-radius: 7px;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 6px 0 6px 0;
            }
            QScrollBar::handle:vertical {
                background: #cfd7e1;
                border-radius: 5px;
                min-height: 40px;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 10px;
                margin: 0 6px 0 6px;
            }
            QScrollBar::handle:horizontal {
                background: #cfd7e1;
                border-radius: 5px;
                min-width: 40px;
            }
            QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {
                background: transparent;
                border: none;
            }
            QSlider::groove:horizontal {
                height: 5px;
                background: #dbe2ea;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #0f6cbd;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 18px;
                margin: -6px 0;
                background: #0f6cbd;
                border-radius: 9px;
            }
            """
        )

    def _restore_initial_folder(self) -> None:
        saved_folder = self.database_service.get_setting("last_folder")
        candidate = Path(saved_folder) if saved_folder else DEFAULT_SCAN_DIR
        if candidate and candidate.exists():
            self.current_folder = str(candidate.resolve())
            self.folder_label.setText(self.current_folder)
        self.refresh_gallery()
        self._refresh_settings()

    def _choose_folder(self) -> None:
        initial_dir = self.current_folder or str(DEFAULT_SCAN_DIR if DEFAULT_SCAN_DIR.exists() else Path.home())
        selected = QFileDialog.getExistingDirectory(self, "选择照片文件夹", initial_dir)
        if not selected:
            return

        self.current_folder = str(Path(selected).resolve())
        self.folder_label.setText(self.current_folder)
        self.database_service.set_setting("last_folder", self.current_folder)
        self.refresh_gallery()
        self._refresh_settings()
        self._update_current_folder()

    def _update_current_folder(self) -> None:
        if not self.current_folder:
            QMessageBox.information(self, "提示", "请先选择一个照片文件夹。")
            return
        self.start_scan(self.current_folder)

    def _rebuild_current_folder(self) -> None:
        if not self.current_folder:
            QMessageBox.information(self, "提示", "请先选择一个照片文件夹。")
            return

        answer = QMessageBox.question(
            self,
            "重新扫描",
            "重新扫描会清空当前目录的索引后重新建立，不会修改原始照片。继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        removed = self.database_service.delete_media_for_folder(self.current_folder)
        self.refresh_gallery(update_status=False)
        self.statusBar().showMessage(f"已清空 {removed} 条索引，正在重新扫描...")
        self.start_scan(self.current_folder)

    def _set_filter(self, value: str) -> None:
        self.gallery_view.clear_selection()
        self.current_filter = value
        for key, button in self.filter_buttons.items():
            button.setChecked(key == value)

        if value == "settings":
            self._refresh_settings()
            self.stack.setCurrentWidget(self.settings_view)
            return

        self.stack.setCurrentWidget(self.gallery_page)
        self.refresh_gallery()

    def refresh_gallery(self, update_status: bool = True) -> None:
        if self.current_filter == "settings":
            self._refresh_settings()
            return

        items = self.database_service.query_media(
            filter_mode=self.current_filter,
            search_text=self.search_edit.text().strip(),
            root_folder=self.current_folder,
        )
        self.current_items = items

        if not self.current_folder:
            empty_text = "请选择照片文件夹开始使用"
        elif self.scan_in_progress:
            empty_text = "正在扫描媒体库，结果会逐步显示..."
        else:
            empty_text = "没有匹配到照片或视频"

        self.gallery_view.set_items(
            items,
            self.group_combo.currentData(),
            self.size_slider.value(),
            empty_text=empty_text,
        )
        self.time_axis.set_items(items)

        if update_status:
            self._show_gallery_status()

    def start_scan(self, folder: str) -> None:
        if self.scan_thread is not None:
            return

        self.scan_in_progress = True
        self.last_live_refresh_count = 0
        self.last_live_refresh_at = 0.0
        self.settings_view.set_busy(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("准备扫描...")
        self.gallery_view.set_loading_state("正在扫描媒体库...")
        self.statusBar().showMessage("正在扫描媒体库...")
        self.refresh_gallery(update_status=False)

        self.scan_thread = QThread(self)
        self.scan_worker = ScanWorker(self.scanner, folder)
        self.scan_worker.moveToThread(self.scan_thread)

        self.scan_thread.started.connect(self.scan_worker.run)
        self.scan_worker.progress.connect(self._on_scan_progress)
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.scan_worker.failed.connect(self._on_scan_failed)
        self.scan_worker.finished.connect(self.scan_thread.quit)
        self.scan_worker.failed.connect(self.scan_thread.quit)
        self.scan_thread.finished.connect(self._cleanup_scan_thread)
        self.scan_thread.start()

    def _refresh_settings(self) -> None:
        self.settings_view.update_context(
            folder=self.current_folder,
            database_path=DATABASE_PATH,
            cache_path=CACHE_DIR,
            ffmpeg_path=self.thumbnail_service.ffmpeg_path,
            stats=self.database_service.get_stats(),
        )

    def _open_preview(self, media_path: str) -> None:
        index = next((i for i, item in enumerate(self.current_items) if item.path == media_path), -1)
        if index < 0:
            return
        dialog = PreviewDialog(
            self.current_items,
            index,
            self.thumbnail_service,
            self.motion_photo_service,
            self,
        )
        dialog.favorite_toggled.connect(self._set_favorite)
        dialog.exec()

    def _jump_to_index(self, index: int) -> None:
        if self.current_filter == "settings":
            return
        self.gallery_view.jump_to_index(index)

    def _sync_time_axis(self, index: int) -> None:
        self.time_axis.sync_to_index(index)

    def _set_favorite(self, media_path: str, value: bool) -> None:
        self._set_favorite_bulk([media_path], value)

    def _set_favorite_bulk(self, media_paths: list[str], value: bool) -> None:
        if not media_paths:
            return

        target_paths = set(media_paths)
        for media_path in target_paths:
            self.database_service.set_favorite(media_path, value)
            self.gallery_view.set_rendered_favorite_state(media_path, value)

        for item in self.current_items:
            if item.path in target_paths:
                item.is_favorite = value

        if self.current_filter == "favorites" and not value:
            self.refresh_gallery()
        else:
            self._refresh_selection_bar()

    def _on_scan_progress(self, current: int, total: int, filename: str) -> None:
        total = max(total, 1)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_bar.setFormat(f"{current}/{total}")

        now = time.monotonic()
        should_refresh = current == self.LIVE_REFRESH_FIRST_BATCH or current == total
        if not should_refresh and current - self.last_live_refresh_count >= self.LIVE_REFRESH_INTERVAL:
            should_refresh = now - self.last_live_refresh_at >= self.LIVE_REFRESH_MIN_SECONDS

        if should_refresh:
            self.last_live_refresh_count = current
            self.last_live_refresh_at = now
            self.refresh_gallery(update_status=False)

        self.statusBar().showMessage(f"扫描中: {filename}")

    def _on_scan_finished(self, summary: dict) -> None:
        self.scan_in_progress = False
        self.gallery_view.set_loading_state(None)
        self.refresh_gallery(update_status=False)
        self.statusBar().showMessage(
            "扫描完成: 新增 {added}，更新 {updated}，跳过 {skipped}，移除 {deleted}".format(**summary),
            6000,
        )
        self._refresh_settings()

    def _on_scan_failed(self, message: str) -> None:
        self.scan_in_progress = False
        self.gallery_view.set_loading_state(None)
        LOGGER.error("Scan failed: %s", message)
        QMessageBox.critical(self, "扫描失败", message)
        self.statusBar().showMessage("扫描失败")

    def _cleanup_scan_thread(self) -> None:
        self.settings_view.set_busy(False)
        self.progress_bar.setVisible(False)
        self.gallery_view.set_loading_state(None)
        if self.scan_worker is not None:
            self.scan_worker.deleteLater()
        if self.scan_thread is not None:
            self.scan_thread.deleteLater()
        self.scan_worker = None
        self.scan_thread = None

    def _on_selection_changed(self, count: int) -> None:
        self._refresh_selection_bar()
        if count:
            self.statusBar().showMessage(f"已选中 {count} 项，按 Ctrl+C 可复制到资源管理器。")
            return
        self._show_gallery_status()

    def _refresh_selection_bar(self) -> None:
        count = self.gallery_view.selection_count
        active = count > 0
        self.selection_bar.setVisible(active)
        self.toolbar_panel.setVisible(not active)
        if not active:
            return

        self.selection_title_label.setText(f"已选择 {count} 项")
        self.selection_favorite_button.setText(
            "取消收藏" if self.gallery_view.all_selected_are_favorite() else "收藏"
        )

    def _copy_selected_items(self) -> None:
        self.gallery_view.copy_selected_to_clipboard()

    def _toggle_selection_favorite(self) -> None:
        selected_paths = self.gallery_view.selected_paths()
        if not selected_paths:
            return
        new_value = not self.gallery_view.all_selected_are_favorite()
        self._set_favorite_bulk(selected_paths, new_value)

    def _show_gallery_status(self) -> None:
        if self.current_filter == "settings":
            return
        if self.gallery_view.selection_count:
            self.statusBar().showMessage(
                f"已选中 {self.gallery_view.selection_count} 项，按 Ctrl+C 可复制到资源管理器。"
            )
            return
        if self.scan_in_progress:
            self.statusBar().showMessage(
                f"正在扫描媒体库，当前可浏览 {self.gallery_view.visible_count} / {len(self.current_items)} 项"
            )
            return
        if self.current_items:
            self.statusBar().showMessage(
                f"当前显示 {self.gallery_view.render_start + 1}-{self.gallery_view.render_end} / {len(self.current_items)} 项"
            )
            return
        self.statusBar().showMessage("没有匹配到照片或视频")
