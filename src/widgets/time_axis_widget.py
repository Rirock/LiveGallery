from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QSlider, QVBoxLayout

from src.models.media_item import MediaItem


class TimeAxisWidget(QFrame):
    jump_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._items: list[MediaItem] = []
        self._dragging = False

        self.setObjectName("TimeAxisPanel")
        self.setFixedWidth(110)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 20, 14, 20)
        layout.setSpacing(12)

        self.title_label = QLabel("时间轴")
        self.title_label.setObjectName("TimelineTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        self.top_label = QLabel("--")
        self.top_label.setObjectName("TimelineEdgeLabel")
        self.top_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.top_label.setWordWrap(True)
        layout.addWidget(self.top_label)

        self.current_label = QLabel("--")
        self.current_label.setObjectName("TimelineCurrentLabel")
        self.current_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_label.setWordWrap(True)
        layout.addWidget(self.current_label)

        self.slider = QSlider(Qt.Orientation.Vertical)
        self.slider.setObjectName("TimelineSlider")
        # Newest media is sorted first, so keep index 0 at the top.
        self.slider.setInvertedAppearance(True)
        self.slider.setMinimum(0)
        self.slider.setMaximum(0)
        self.slider.valueChanged.connect(self._on_value_changed)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self.slider, stretch=1)

        self.bottom_label = QLabel("--")
        self.bottom_label.setObjectName("TimelineEdgeLabel")
        self.bottom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bottom_label.setWordWrap(True)
        layout.addWidget(self.bottom_label)

        self.setEnabled(False)

    def set_items(self, items: list[MediaItem]) -> None:
        self._items = items
        self.setEnabled(bool(items))
        if not items:
            with QSignalBlocker(self.slider):
                self.slider.setMinimum(0)
                self.slider.setMaximum(0)
                self.slider.setValue(0)
            self.top_label.setText("--")
            self.current_label.setText("--")
            self.bottom_label.setText("--")
            return

        with QSignalBlocker(self.slider):
            self.slider.setMinimum(0)
            self.slider.setMaximum(len(items) - 1)
            self.slider.setValue(0)

        self.top_label.setText(items[0].capture_time.strftime("%Y\n%m-%d"))
        self.bottom_label.setText(items[-1].capture_time.strftime("%Y\n%m-%d"))
        self.current_label.setText(items[0].capture_time.strftime("%Y年%m月"))

    def sync_to_index(self, index: int) -> None:
        if not self._items or self._dragging:
            return

        index = max(0, min(index, len(self._items) - 1))
        self.current_label.setText(self._items[index].capture_time.strftime("%Y年%m月"))
        with QSignalBlocker(self.slider):
            self.slider.setValue(index)

    def _on_value_changed(self, value: int) -> None:
        if not self._items:
            return
        value = max(0, min(value, len(self._items) - 1))
        self.current_label.setText(self._items[value].capture_time.strftime("%Y年%m月"))

    def _on_slider_pressed(self) -> None:
        self._dragging = True

    def _on_slider_released(self) -> None:
        self._dragging = False
        self.jump_requested.emit(self.slider.value())
