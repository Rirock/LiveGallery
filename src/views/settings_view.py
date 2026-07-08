from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SettingsView(QWidget):
    choose_folder_requested = Signal()
    update_requested = Signal()
    rebuild_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SettingsView")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(16)

        header = QLabel("设置")
        header.setObjectName("SettingsTitle")
        layout.addWidget(header)

        actions = QFrame()
        actions.setObjectName("SettingsCard")
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(18, 18, 18, 18)
        actions_layout.setSpacing(12)

        self.choose_button = QPushButton("选择文件夹")
        self.choose_button.setObjectName("PrimaryButton")
        self.choose_button.clicked.connect(self.choose_folder_requested)
        actions_layout.addWidget(self.choose_button)

        self.update_button = QPushButton("更新")
        self.update_button.setObjectName("SecondaryButton")
        self.update_button.clicked.connect(self.update_requested)
        actions_layout.addWidget(self.update_button)

        self.rebuild_button = QPushButton("重新扫描")
        self.rebuild_button.setObjectName("DangerButton")
        self.rebuild_button.clicked.connect(self.rebuild_requested)
        actions_layout.addWidget(self.rebuild_button)
        actions_layout.addStretch(1)
        layout.addWidget(actions)

        info_card = QFrame()
        info_card.setObjectName("SettingsCard")
        info_layout = QGridLayout(info_card)
        info_layout.setContentsMargins(18, 18, 18, 18)
        info_layout.setHorizontalSpacing(18)
        info_layout.setVerticalSpacing(12)

        self.folder_label = QLabel()
        self.database_label = QLabel()
        self.cache_label = QLabel()
        self.ffmpeg_label = QLabel()
        self.stats_label = QLabel()
        self.safety_label = QLabel("原始照片只读，不会被修改、删除或移动。")
        self.source_label = QLabel(
            '来自 <a href="https://github.com/Rirock/LiveGallery">Rirock/LiveGallery</a>'
        )
        self.source_label.setOpenExternalLinks(True)
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)

        rows = (
            ("照片目录", self.folder_label),
            ("SQLite", self.database_label),
            ("缓存目录", self.cache_label),
            ("ffmpeg", self.ffmpeg_label),
            ("索引统计", self.stats_label),
            ("安全", self.safety_label),
            ("项目", self.source_label),
        )
        for row, (label_text, value_widget) in enumerate(rows):
            key = QLabel(label_text)
            key.setObjectName("SettingsKey")
            value_widget.setWordWrap(True)
            value_widget.setObjectName("SettingsText")
            info_layout.addWidget(key, row, 0)
            info_layout.addWidget(value_widget, row, 1)
        info_layout.setColumnStretch(1, 1)
        layout.addWidget(info_card)
        layout.addStretch(1)

    def set_busy(self, busy: bool) -> None:
        self.choose_button.setEnabled(not busy)
        self.update_button.setEnabled(not busy)
        self.rebuild_button.setEnabled(not busy)

    def update_context(
        self,
        folder: str | None,
        database_path: Path,
        cache_path: Path,
        ffmpeg_path: str | None,
        stats: dict[str, int],
    ) -> None:
        self.folder_label.setText(folder or "未选择")
        self.database_label.setText(str(database_path))
        self.cache_label.setText(str(cache_path))
        self.ffmpeg_label.setText(ffmpeg_path or "未找到，可安装 imageio-ffmpeg 或系统 ffmpeg")
        self.stats_label.setText(
            "{total} 项 | 动态照片 {motion} | 视频 {videos} | 收藏 {favorites}".format(**stats)
        )
