"""Windows system tray icon for ChangeSnap."""

from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PySide6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor, QFont
from PySide6.QtCore import Qt, QTimer, Signal, QObject


class SystemTray(QObject):
    """System tray icon with context menu for ChangeSnap."""

    # Signals that the main window connects to
    capture_and_advance = Signal()
    capture_only = Signal()
    previous_step = Signal()
    toggle_pause = Signal()
    stop_session = Signal()
    show_main_window = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tray = QSystemTrayIcon(self)
        self._tray.setToolTip("ChangeSnap - 变更报告助手")

        # Create initial icon
        self._tray.setIcon(self._make_icon("idle"))

        # Build menu
        self._menu = QMenu()
        self._rebuild_menu("idle")
        self._tray.setContextMenu(self._menu)

        # Timer for elapsed time display
        self._elapsed = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_time)

        self._tray.show()

    def _make_icon(self, state: str) -> QIcon:
        """Create a simple colored dot icon.

        States: 'idle' (gray), 'recording' (red), 'paused' (orange), 'reviewing' (green)
        """
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        colors = {
            'idle': QColor(128, 128, 128),
            'recording': QColor(255, 59, 48),
            'paused': QColor(255, 149, 0),
            'reviewing': QColor(52, 199, 89),
        }
        color = colors.get(state, QColor(128, 128, 128))
        painter.setBrush(color)
        painter.setPen(QColor(0, 0, 0, 0))
        painter.drawEllipse(4, 4, 24, 24)
        painter.end()
        return QIcon(pixmap)

    def _rebuild_menu(self, phase: str, step_info=""):
        """Rebuild context menu based on app phase."""
        self._menu.clear()

        title = self._menu.addAction("ChangeSnap 变更报告助手")
        title.setEnabled(False)
        self._menu.addSeparator()

        if phase in ('idle', 'loading'):
            self._menu.addAction("打开主窗口", self.show_main_window.emit)
            self._menu.addSeparator()
            self._menu.addAction("退出", QApplication.instance().quit)
        elif phase in ('confirmation', 'ready'):
            self._menu.addAction("打开主窗口", self.show_main_window.emit)
        elif phase == 'running':
            if step_info:
                self._menu.addAction(step_info).setEnabled(False)
                self._menu.addSeparator()
            self._menu.addAction("截图并前进 (Ctrl+8)", self.capture_and_advance.emit)
            self._menu.addAction("仅截图 (Ctrl+9)", self.capture_only.emit)
            self._menu.addAction("上一步 (Ctrl+7)", self.previous_step.emit)
            self._menu.addAction("暂停/恢复 (Ctrl+0)", self.toggle_pause.emit)
            self._menu.addSeparator()
            self._menu.addAction("停止变更 (Ctrl+Shift+S)", self.stop_session.emit)
            self._menu.addSeparator()
            self._menu.addAction("打开主窗口", self.show_main_window.emit)
        elif phase == 'reviewing':
            self._menu.addAction("打开主窗口", self.show_main_window.emit)

        self._menu.addSeparator()
        self._menu.addAction("退出", QApplication.instance().quit)

    def update_state(self, phase: str, step_text: str = "", step_idx: int = 0, total: int = 0, elapsed: int = 0):
        """Update tray icon and menu based on current app state."""
        if phase == 'running':
            self._tray.setIcon(self._make_icon('recording'))
            step_info = f"录制中 {self._fmt_time(elapsed)} | 步骤 {step_idx}/{total}"
            self._elapsed = elapsed
            if not self._timer.isActive():
                self._timer.start(1000)
        elif phase == 'paused':
            self._tray.setIcon(self._make_icon('paused'))
            step_info = f"已暂停 | 步骤 {step_idx}/{total}"
        elif phase == 'reviewing':
            self._tray.setIcon(self._make_icon('reviewing'))
            step_info = ""
        else:
            self._tray.setIcon(self._make_icon('idle'))
            step_info = ""
            self._timer.stop()

        self._rebuild_menu(phase, step_info)

    def _fmt_time(self, seconds: int) -> str:
        h, m = divmod(seconds, 3600)
        m, s = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _update_time(self):
        self._elapsed += 1


# Also create a simple AppIcon for the main window
def create_app_icon() -> QIcon:
    """Create a simple application icon."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(0, 122, 255))
    painter.setPen(QColor(0, 0, 0, 0))
    painter.drawRoundedRect(4, 4, 56, 56, 12, 12)
    painter.setPen(QColor(255, 255, 255))
    font = QFont("Microsoft YaHei", 20, QFont.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "CS")
    painter.end()
    return QIcon(pixmap)
