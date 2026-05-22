"""Windows-compatible screen region selector using PySide6 native widgets.

Replaces the macOS-only ``screencapture -i`` subprocess approach for ChangeSnap.
Provides a full-screen semi-transparent overlay across all monitors that allows
the user to drag-select a rectangular region (like Windows Snipping Tool or
macOS Cmd+Shift+4).

Usage::

    from region_selector import RegionSelector

    region = RegionSelector.get_region()
    if region is not None:
        left, top, width, height = region["left"], region["top"], region["width"], region["height"]
    else:
        # User cancelled
"""

from __future__ import annotations

from PySide6.QtCore import QEventLoop, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QApplication, QLabel, QWidget


class RegionSelector(QWidget):
    """Full-screen overlay for selecting a screen region.

    Covers the virtual desktop spanning all monitors.  The user drags a
    rectangular selection; on release the chosen region is emitted via the
    ``region_selected`` signal and the widget closes.  Pressing Escape or
    dragging a region smaller than 20x20 pixels cancels the selection
    (emits ``None``).

    Signals
    -------
    region_selected : object
        Emits a ``dict`` with keys ``left``, ``top``, ``width``, ``height``
        (global screen coordinates), or ``None`` if cancelled.
    """

    region_selected = Signal(object)  # dict | None

    def __init__(self) -> None:
        super().__init__()

        # -- Window flags: frameless, always-on-top, no taskbar entry ----------
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        # -- Internal state ----------------------------------------------------
        self._start: QPoint | None = None
        self._current: QPoint | None = None
        self._is_dragging: bool = False
        self._result: dict | None = None

        # -- Geometry: cover the entire virtual desktop ------------------------
        screens = QApplication.screens()
        self._virtual_geo: QRect = QApplication.primaryScreen().virtualGeometry()
        self.setGeometry(self._virtual_geo)

        # -- Fonts -------------------------------------------------------------
        self._mono_font: QFont = self._get_mono_font()

    # ------------------------------------------------------------------
    # Font helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_mono_font() -> QFont:
        """Return a monospaced font suitable for dimension labels."""
        font = QFont("Consolas", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        return font

    # ------------------------------------------------------------------
    # Mouse event handlers
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        """Begin dragging a selection rectangle."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._current = self._start
            self._is_dragging = True
            self.update()

    def mouseMoveEvent(self, event) -> None:
        """Update the selection rectangle while dragging."""
        if self._is_dragging:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        """Finalise the selection when the user releases the mouse."""
        if event.button() == Qt.MouseButton.LeftButton and self._is_dragging:
            self._is_dragging = False
            rect = self._normalized_rect()

            # Ignore selections smaller than 20x20 px (treat as cancel).
            if rect.width() > 20 and rect.height() > 20:
                # Coordinates are already in virtual-geometry space, which
                # IS the global screen coordinate space on Windows.
                self._result = {
                    "left": rect.x(),
                    "top": rect.y(),
                    "width": rect.width(),
                    "height": rect.height(),
                }
            else:
                self._result = None

            self.region_selected.emit(self._result)
            self.close()

    # ------------------------------------------------------------------
    # Keyboard event handlers
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Cancel selection on Escape."""
        if event.key() == Qt.Key.Key_Escape:
            self._result = None
            self.region_selected.emit(None)
            self.close()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        """Render the overlay: dimmed background + selection rectangle."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # -- Full-screen semi-transparent black (~30 % opacity) ---------------
        painter.fillRect(self.rect(), QColor(0, 0, 0, 77))

        # -- Selection rectangle (only visible while dragging) ----------------
        if self._is_dragging and self._start is not None and self._current is not None:
            rect = self._normalized_rect()

            # Very light blue fill inside the selected area.
            painter.fillRect(rect, QColor(0, 122, 255, 25))

            # 2 px solid blue border.
            pen = QPen(QColor(0, 122, 255), 2)
            pen.setStyle(Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawRect(rect)

            # -- Dimension label (e.g. "800 x 600") ---------------------------
            label = f"{rect.width()} x {rect.height()}"
            label_rect = painter.boundingRect(QRect(), Qt.AlignmentFlag.AlignCenter, label)

            # Position the label above the selection, or below if it would
            # overflow the top of the screen.
            label_x = rect.center().x() - label_rect.width() // 2 - 6
            label_y = rect.top() - 28
            if label_y < 0:
                label_y = rect.bottom() + 8

            # Semi-transparent dark background for readability.
            painter.fillRect(
                label_x,
                label_y,
                label_rect.width() + 12,
                label_rect.height() + 8,
                QColor(0, 0, 0, 160),
            )
            painter.setPen(QColor(255, 255, 255))
            painter.setFont(self._mono_font)
            painter.drawText(
                label_x + 6,
                label_y + label_rect.height(),
                label,
            )

        # -- Center instruction text -------------------------------------------
        instruction = "拖动鼠标选择录屏区域    Esc 取消"
        inst_font = QFont("Microsoft YaHei", 16, QFont.Bold)
        painter.setFont(inst_font)
        inst_rect = painter.boundingRect(QRect(), Qt.AlignmentFlag.AlignCenter, instruction)

        cx = self.rect().center().x()
        cy = self.rect().center().y()
        inst_x = cx - inst_rect.width() // 2 - 24
        inst_y = cy + self.rect().height() // 4  # below center

        # Semi-transparent background pill
        bg_x = inst_x - 12
        bg_y = inst_y - 8
        bg_w = inst_rect.width() + 48
        bg_h = inst_rect.height() + 32
        painter.fillRect(bg_x, bg_y, bg_w, bg_h, QColor(0, 0, 0, 160))

        painter.setPen(QColor(255, 255, 255, 240))
        painter.drawText(
            inst_x + 24,
            inst_y + inst_rect.height() + 4,
            instruction,
        )

        painter.end()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalized_rect(self) -> QRect:
        """Return a `QRect` with positive width/height from any drag direction.

        This ensures the rectangle is always well-formed regardless of whether
        the user dragged left-to-right, right-to-left, top-to-bottom, etc.
        """
        x1, y1 = self._start.x(), self._start.y()
        x2, y2 = self._current.x(), self._current.y()
        return QRect(
            min(x1, x2),
            min(y1, y2),
            abs(x2 - x1),
            abs(y2 - y1),
        )

    # ------------------------------------------------------------------
    # Static convenience API (blocking)
    # ------------------------------------------------------------------

    @staticmethod
    def get_region() -> dict | None:
        """Show the region selector and block until the user selects or cancels.

        This is a synchronous convenience wrapper around creating,
        showing, and running the event loop for a `RegionSelector`.

        Returns
        -------
        dict or None
            A dictionary with keys ``left``, ``top``, ``width``, ``height``
            in global screen coordinates, or ``None`` if the user cancelled
            (Escape or sub-20x20 drag).
        """
        selector = RegionSelector()
        loop = QEventLoop()
        result_container: dict[str, dict | None] = {}

        def _on_selected(rect: dict | None) -> None:
            result_container["rect"] = rect
            loop.quit()

        selector.region_selected.connect(_on_selected)
        selector.show()
        loop.exec()

        return result_container.get("rect")
