from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, QSignalBlocker, QThreadPool, QUrl, Qt, Signal
from PySide6.QtGui import QImage, QKeyEvent, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.models.media_item import MediaItem
from src.services.motion_photo_service import MotionPhotoService
from src.services.thumbnail_service import ThumbnailService
from src.widgets.loading_overlay import LoadingOverlay
from src.widgets.zoomable_image_view import ZoomableImageView


class StableVideoLabel(QLabel):
    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        # Ignore pixmap size hints so incoming video frames never push the dialog larger.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(420, 260)

    def sizeHint(self) -> QSize:
        return QSize(960, 540)

    def minimumSizeHint(self) -> QSize:
        return QSize(420, 260)


class PreviewImageTaskSignals(QObject):
    finished = Signal(int, str, object)


class PreviewImageTask(QRunnable):
    def __init__(self, service: ThumbnailService, image_path: str, request_id: int) -> None:
        super().__init__()
        self.service = service
        self.image_path = image_path
        self.request_id = request_id
        self.signals = PreviewImageTaskSignals()

    def run(self) -> None:
        image = self.service.load_preview_image(self.image_path)
        try:
            self.signals.finished.emit(self.request_id, self.image_path, image)
        except RuntimeError:
            return


class PreviewDialog(QDialog):
    favorite_toggled = Signal(str, bool)

    def __init__(
        self,
        items: list[MediaItem],
        current_index: int,
        thumbnail_service: ThumbnailService,
        motion_photo_service: MotionPhotoService | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.items = items
        self.current_index = current_index
        self.thumbnail_service = thumbnail_service
        self.motion_photo_service = motion_photo_service
        self.return_to_photo_on_end = False
        self._slider_dragging = False
        self._current_video_pixmap = QPixmap()
        self._preview_request_id = 0
        self._awaiting_video_frame = False
        self.thread_pool = QThreadPool.globalInstance()

        self.setWindowTitle("预览")
        self.resize(1320, 900)
        self.setMinimumSize(1040, 720)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(
            """
            QDialog {
                background: #0b0f14;
            }
            QLabel#PreviewTitle {
                color: #f8fafc;
                font-size: 18px;
                font-weight: 800;
            }
            QLabel#PreviewMeta, QLabel#VideoTimeLabel, QLabel#ZoomLabel {
                color: #98a2b3;
                font-size: 12px;
                font-weight: 600;
            }
            QScrollArea#ZoomableImageView {
                background: transparent;
                border: none;
            }
            QLabel#PreviewImage {
                background: #0f141b;
                border: 1px solid #1f2937;
                border-radius: 22px;
                color: #667085;
            }
            QPushButton, QToolButton {
                background: rgba(255, 255, 255, 0.08);
                color: #f8fafc;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 14px;
                padding: 10px 16px;
                font-weight: 600;
            }
            QPushButton:hover, QToolButton:hover {
                background: rgba(255, 255, 255, 0.12);
                border-color: rgba(255, 255, 255, 0.18);
            }
            QPushButton:pressed, QToolButton:pressed {
                background: rgba(255, 255, 255, 0.16);
            }
            QPushButton#AccentButton {
                background: #f8fafc;
                color: #0f172a;
                border-color: #f8fafc;
            }
            QPushButton#AccentButton:hover {
                background: #e5e7eb;
                border-color: #e5e7eb;
            }
            QToolButton#EdgeNavButton {
                background: rgba(255, 255, 255, 0.08);
                color: #f8fafc;
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 28px;
                padding: 0;
                font-size: 28px;
                font-weight: 700;
            }
            QToolButton#EdgeNavButton:hover {
                background: rgba(255, 255, 255, 0.14);
                border-color: rgba(255, 255, 255, 0.24);
            }
            QToolButton#EdgeNavButton:disabled {
                color: rgba(255, 255, 255, 0.28);
                border-color: rgba(255, 255, 255, 0.08);
            }
            QToolButton#PreviewFavoriteButton {
                background: rgba(255, 255, 255, 0.08);
                color: #e5e7eb;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 22px;
                padding: 0;
                min-width: 44px;
                max-width: 44px;
                min-height: 44px;
                max-height: 44px;
                font-size: 24px;
                font-weight: 700;
            }
            QToolButton#PreviewFavoriteButton:checked {
                background: rgba(255, 247, 221, 0.16);
                color: #f4c542;
                border-color: rgba(244, 197, 66, 0.38);
            }
            QLabel#PreviewVideo {
                background: #04070b;
                border: 1px solid #1f2937;
                border-radius: 22px;
                color: #94a3b8;
            }
            QSlider::groove:horizontal {
                height: 5px;
                background: rgba(255, 255, 255, 0.16);
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #f8fafc;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 16px;
                margin: -6px 0;
                background: #f8fafc;
                border-radius: 8px;
            }
            """
        )

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(22, 20, 22, 22)
        root_layout.setSpacing(16)

        header_layout = QHBoxLayout()

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.info_label = QLabel()
        self.info_label.setObjectName("PreviewTitle")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_box.addWidget(self.info_label)

        self.meta_label = QLabel()
        self.meta_label.setObjectName("PreviewMeta")
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_box.addWidget(self.meta_label)
        header_layout.addLayout(title_box, stretch=1)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("ZoomLabel")
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self.zoom_label)

        self.favorite_button = QToolButton()
        self.favorite_button.setObjectName("PreviewFavoriteButton")
        self.favorite_button.setCheckable(True)
        self.favorite_button.clicked.connect(self._on_favorite_clicked)
        header_layout.addWidget(self.favorite_button)

        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.close)
        header_layout.addWidget(self.close_button)
        root_layout.addLayout(header_layout)

        preview_layout = QHBoxLayout()
        preview_layout.setSpacing(16)
        root_layout.addLayout(preview_layout, stretch=1)

        self.prev_button = QToolButton()
        self.prev_button.setObjectName("EdgeNavButton")
        self.prev_button.setText("‹")
        self.prev_button.setFixedSize(56, 56)
        self.prev_button.clicked.connect(self.show_previous)
        preview_layout.addWidget(self.prev_button, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.stack = QStackedWidget()
        preview_layout.addWidget(self.stack, stretch=1)

        self.next_button = QToolButton()
        self.next_button.setObjectName("EdgeNavButton")
        self.next_button.setText("›")
        self.next_button.setFixedSize(56, 56)
        self.next_button.clicked.connect(self.show_next)
        preview_layout.addWidget(self.next_button, alignment=Qt.AlignmentFlag.AlignVCenter)

        image_page = QWidget()
        self.image_page = image_page
        image_layout = QVBoxLayout(image_page)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(12)

        self.image_view = ZoomableImageView()
        self.image_view.zoom_changed.connect(self._on_zoom_changed)
        image_layout.addWidget(self.image_view, stretch=1)

        image_controls = QHBoxLayout()
        image_controls.addStretch(1)
        self.play_motion_button = QPushButton("播放动态照片")
        self.play_motion_button.setObjectName("AccentButton")
        self.play_motion_button.clicked.connect(self.play_motion_photo)
        image_controls.addWidget(self.play_motion_button)

        self.reset_zoom_button = QPushButton("重置缩放")
        self.reset_zoom_button.clicked.connect(self.image_view.reset_zoom)
        image_controls.addWidget(self.reset_zoom_button)
        image_controls.addStretch(1)
        image_layout.addLayout(image_controls)
        self.stack.addWidget(image_page)

        video_page = QWidget()
        self.video_page = video_page
        video_layout = QVBoxLayout(video_page)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(12)

        self.video_label = StableVideoLabel("正在载入视频...")
        self.video_label.setObjectName("PreviewVideo")
        video_layout.addWidget(self.video_label, stretch=1)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)
        self.play_pause_button = QPushButton("暂停")
        self.play_pause_button.setObjectName("AccentButton")
        self.play_pause_button.clicked.connect(self._toggle_play_pause)
        controls_row.addWidget(self.play_pause_button)

        self.position_label = QLabel("00:00")
        self.position_label.setObjectName("VideoTimeLabel")
        controls_row.addWidget(self.position_label)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderPressed.connect(self._on_slider_pressed)
        self.position_slider.sliderReleased.connect(self._on_slider_released)
        self.position_slider.sliderMoved.connect(self._on_slider_moved)
        controls_row.addWidget(self.position_slider, stretch=1)

        self.duration_label = QLabel("00:00")
        self.duration_label.setObjectName("VideoTimeLabel")
        controls_row.addWidget(self.duration_label)

        self.back_to_photo_button = QPushButton("返回静态照片")
        self.back_to_photo_button.clicked.connect(self.show_static_image)
        controls_row.addWidget(self.back_to_photo_button)
        video_layout.addLayout(controls_row)
        self.stack.addWidget(video_page)
        self.loading_overlay = LoadingOverlay(self.stack, message="正在载入内容...", dark=True)

        self.audio_output = QAudioOutput()
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.video_sink = QVideoSink(self)
        self.video_sink.videoFrameChanged.connect(self._on_video_frame_changed)
        self.player.setVideoOutput(self.video_sink)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_player_error)
        self._bind_shortcuts()

        self._load_current_item()

    def _bind_shortcuts(self) -> None:
        self.previous_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self.previous_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.previous_shortcut.activated.connect(self.show_previous)

        self.next_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        self.next_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.next_shortcut.activated.connect(self.show_next)

        self.close_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.close_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.close_shortcut.activated.connect(self.close)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        super().keyPressEvent(event)

    def show_previous(self) -> None:
        if self.current_index > 0:
            self.current_index -= 1
            self._load_current_item()

    def show_next(self) -> None:
        if self.current_index < len(self.items) - 1:
            self.current_index += 1
            self._load_current_item()

    def play_motion_photo(self) -> None:
        item = self.items[self.current_index]
        if not item.is_motion_photo or self.motion_photo_service is None:
            return
        self.loading_overlay.show_message("正在准备动态照片...", block_input=False)
        self.play_motion_button.setEnabled(False)
        video_path = self.motion_photo_service.extract_embedded_video(
            Path(item.path),
            item.file_size,
            item.file_mtime,
        )
        self.play_motion_button.setEnabled(True)
        if video_path is None or not video_path.exists():
            self.loading_overlay.hide_overlay()
            self.image_view.show_message("未找到可播放的动态照片视频")
            return
        item.motion_video_path = str(video_path)
        self._show_video(str(video_path), return_to_photo_on_end=True)

    def show_static_image(self) -> None:
        self.return_to_photo_on_end = False
        self._awaiting_video_frame = False
        self.player.stop()
        self.stack.setCurrentIndex(0)
        self.zoom_label.show()
        self.back_to_photo_button.hide()
        self.loading_overlay.hide_overlay()

    def closeEvent(self, event) -> None:
        self._preview_request_id += 1
        self.player.stop()
        self.loading_overlay.hide_overlay()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.stack.currentIndex() == 1:
            self._refresh_video_frame()

    def _load_current_item(self) -> None:
        self._preview_request_id += 1
        self.return_to_photo_on_end = False
        self._awaiting_video_frame = False
        self.player.stop()
        self._reset_video_controls()
        self._current_video_pixmap = QPixmap()
        self.video_label.setPixmap(QPixmap())
        self.video_label.setText("正在载入视频...")

        item = self.items[self.current_index]
        self.favorite_button.setChecked(item.is_favorite)
        self._update_favorite_button_text()
        self.prev_button.setEnabled(self.current_index > 0)
        self.next_button.setEnabled(self.current_index < len(self.items) - 1)
        self.info_label.setText(item.filename)
        self.meta_label.setText(item.capture_time_display)

        if item.media_type == "video":
            self.zoom_label.hide()
            self.play_motion_button.hide()
            self.reset_zoom_button.hide()
            self._show_video(item.path, return_to_photo_on_end=False)
            return

        self.stack.setCurrentIndex(0)
        self.zoom_label.show()
        self.play_motion_button.show()
        self.reset_zoom_button.show()
        self.back_to_photo_button.hide()
        self.image_view.show_message("")
        self.loading_overlay.show_message("正在载入照片...", block_input=False)
        self._start_image_loading(item.path, self._preview_request_id)

        self.play_motion_button.setVisible(item.is_motion_photo)

    def _show_video(self, video_path: str, return_to_photo_on_end: bool) -> None:
        self.return_to_photo_on_end = return_to_photo_on_end
        self._awaiting_video_frame = True
        self.stack.setCurrentIndex(1)
        self.zoom_label.hide()
        self.back_to_photo_button.setVisible(return_to_photo_on_end)
        self.video_label.setText("正在载入视频...")
        self.video_label.setPixmap(QPixmap())
        self._current_video_pixmap = QPixmap()
        self.loading_overlay.show_message("正在载入视频...", block_input=False)
        self.player.setSource(QUrl.fromLocalFile(video_path))
        self.player.play()

    def _start_image_loading(self, image_path: str, request_id: int) -> None:
        task = PreviewImageTask(self.thumbnail_service, image_path, request_id)
        task.signals.finished.connect(self._on_preview_image_loaded)
        self.thread_pool.start(task)

    def _toggle_play_pause(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_slider_pressed(self) -> None:
        self._slider_dragging = True

    def _on_slider_released(self) -> None:
        self._slider_dragging = False
        self.player.setPosition(self.position_slider.value())

    def _on_slider_moved(self, value: int) -> None:
        self.position_label.setText(self._format_ms(value))

    def _on_position_changed(self, value: int) -> None:
        if not self._slider_dragging:
            with QSignalBlocker(self.position_slider):
                self.position_slider.setValue(value)
        self.position_label.setText(self._format_ms(value))

    def _on_duration_changed(self, value: int) -> None:
        with QSignalBlocker(self.position_slider):
            self.position_slider.setRange(0, max(0, value))
        self.duration_label.setText(self._format_ms(value))

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_pause_button.setText("暂停" if state == QMediaPlayer.PlaybackState.PlayingState else "播放")

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.return_to_photo_on_end:
            self.show_static_image()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.loading_overlay.hide_overlay()
            self._awaiting_video_frame = False
            self.video_label.setText("无法播放该视频")
        elif status == QMediaPlayer.MediaStatus.LoadingMedia:
            self.loading_overlay.show_message("正在载入视频...", block_input=False)

    def _on_zoom_changed(self, scale: float) -> None:
        self.zoom_label.setText(f"{int(scale * 100)}%")

    def _reset_video_controls(self) -> None:
        with QSignalBlocker(self.position_slider):
            self.position_slider.setRange(0, 0)
            self.position_slider.setValue(0)
        self.position_label.setText("00:00")
        self.duration_label.setText("00:00")
        self.play_pause_button.setText("暂停")

    def _on_favorite_clicked(self, checked: bool) -> None:
        item = self.items[self.current_index]
        item.is_favorite = checked
        self._update_favorite_button_text()
        self.favorite_toggled.emit(item.path, checked)

    def _on_video_frame_changed(self, frame) -> None:
        if not frame.isValid():
            return

        image = frame.toImage()
        if image.isNull():
            return

        if image.format() != QImage.Format.Format_RGBA8888:
            image = image.convertToFormat(QImage.Format.Format_RGBA8888)

        self._current_video_pixmap = QPixmap.fromImage(image)
        if self._awaiting_video_frame:
            self._awaiting_video_frame = False
            self.loading_overlay.hide_overlay()
        self._refresh_video_frame()

    def _on_preview_image_loaded(self, request_id: int, image_path: str, image: object) -> None:
        if request_id != self._preview_request_id:
            return
        if self.current_index >= len(self.items):
            return
        current_item = self.items[self.current_index]
        if current_item.path != image_path or current_item.media_type == "video":
            return

        self.loading_overlay.hide_overlay()
        if isinstance(image, QImage) and not image.isNull():
            self.image_view.set_pixmap(QPixmap.fromImage(image))
            return
        self.image_view.show_message("无法加载图片预览")

    def _refresh_video_frame(self) -> None:
        if self._current_video_pixmap.isNull():
            return

        target_size = self.video_label.contentsRect().size()
        if target_size.width() <= 0 or target_size.height() <= 0:
            return

        scaled = self._current_video_pixmap.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.video_label.setPixmap(scaled)
        self.video_label.setText("")

    def _on_player_error(self, _error) -> None:
        error_text = self.player.errorString().strip() or "无法播放该视频"
        if self.stack.currentIndex() == 1:
            self.loading_overlay.hide_overlay()
            self._awaiting_video_frame = False
            self.video_label.setText(error_text)

    def _update_favorite_button_text(self) -> None:
        is_checked = self.favorite_button.isChecked()
        self.favorite_button.setText("★" if is_checked else "☆")
        self.favorite_button.setToolTip("取消收藏" if is_checked else "收藏")

    @staticmethod
    def _format_ms(value: int) -> str:
        total_seconds = max(0, value // 1000)
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"
