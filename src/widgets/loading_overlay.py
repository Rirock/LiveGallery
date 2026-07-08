from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class LoadingSpinner(QWidget):
    def __init__(
        self,
        diameter: int = 42,
        line_width: int = 4,
        color: str = "#111827",
        trail_color: str = "#d5dde7",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._diameter = diameter
        self._line_width = line_width
        self._color = QColor(color)
        self._trail_color = QColor(trail_color)
        self._angle = 0

        self.setFixedSize(diameter, diameter)

        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._advance)
        self._timer.start()

    def sizeHint(self) -> QSize:
        return QSize(self._diameter, self._diameter)

    def set_colors(self, color: str, trail_color: str) -> None:
        self._color = QColor(color)
        self._trail_color = QColor(trail_color)
        self.update()

    def _advance(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event) -> None:
        del event
        side = min(self.width(), self.height())
        margin = self._line_width
        arc_rect = self.rect().adjusted(margin, margin, -margin, -margin)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        trail_pen = QPen(self._trail_color, self._line_width)
        trail_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(trail_pen)
        painter.drawArc(arc_rect, 0, 360 * 16)

        active_pen = QPen(self._color, self._line_width)
        active_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(active_pen)
        painter.drawArc(arc_rect, -self._angle * 16, -120 * 16)


class LoadingOverlay(QFrame):
    def __init__(
        self,
        parent: QWidget,
        *,
        message: str = "正在加载...",
        dark: bool = False,
        block_input: bool = False,
    ) -> None:
        super().__init__(parent)
        self._dark = dark

        self.setObjectName("LoadingOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not block_input)
        self.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)

        self.card = QFrame(self)
        self.card.setObjectName("LoadingOverlayCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(22, 20, 22, 20)
        card_layout.setSpacing(12)

        spinner_color = "#f8fafc" if dark else "#111827"
        spinner_trail = "#475467" if dark else "#d5dde7"
        self.spinner = LoadingSpinner(color=spinner_color, trail_color=spinner_trail, parent=self.card)
        card_layout.addWidget(self.spinner, alignment=Qt.AlignmentFlag.AlignCenter)

        self.label = QLabel(message, self.card)
        self.label.setObjectName("LoadingOverlayLabel")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        card_layout.addWidget(self.label)

        layout.addWidget(self.card, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)

        self._apply_theme()
        parent.installEventFilter(self)
        self._sync_geometry()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.parent() and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Move,
            QEvent.Type.Show,
        }:
            self._sync_geometry()
        return super().eventFilter(watched, event)

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        self._apply_theme()

    def show_message(self, message: str | None = None, *, block_input: bool | None = None) -> None:
        if message:
            self.label.setText(message)
        if block_input is not None:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not block_input)
        self._sync_geometry()
        self.raise_()
        self.show()

    def hide_overlay(self) -> None:
        self.hide()

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())

    def _apply_theme(self) -> None:
        if self._dark:
            self.spinner.set_colors("#f8fafc", "#475467")
            self.setStyleSheet(
                """
                QFrame#LoadingOverlay {
                    background: rgba(15, 23, 42, 72);
                }
                QFrame#LoadingOverlayCard {
                    background: rgba(15, 23, 42, 232);
                    border: 1px solid rgba(255, 255, 255, 22);
                    border-radius: 22px;
                }
                QLabel#LoadingOverlayLabel {
                    color: #f8fafc;
                    font-size: 14px;
                    font-weight: 700;
                }
                """
            )
            return

        self.spinner.set_colors("#111827", "#d5dde7")
        self.setStyleSheet(
            """
            QFrame#LoadingOverlay {
                background: rgba(255, 255, 255, 88);
            }
            QFrame#LoadingOverlayCard {
                background: rgba(255, 255, 255, 240);
                border: 1px solid #dbe3ec;
                border-radius: 20px;
            }
            QLabel#LoadingOverlayLabel {
                color: #111827;
                font-size: 14px;
                font-weight: 700;
            }
            """
        )
