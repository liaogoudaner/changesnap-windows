"""MiniFloatingWindow — 浮动操作栏 (macOS MiniFloatingWindowView 的 Windows PySide6 实现)。

完全仿照 macOS 版 MiniFloatingWindowView 的布局与视觉风格。

布局结构::

    ┌──────────────────────────────┐
    │  ● 00:23:45                  │  ← Recording indicator + timer
    ├──────────────────────────────┤
    │  ▶ 步骤 3/8                  │  ← Current step label
    │                              │
    │  更新后端包 third-user-      │  ← Step description (scrollable)
    │  sync.jar                    │
    │                              │
    │  📷 4 张截图                  │  ← Screenshot count
    ├──────────────────────────────┤
    │  ┌────────────────────────┐  │
    │  │  截图并前进             │  │  ← Primary button (prominent)
    │  │  Ctrl+8                │  │
    │  └────────────────────────┘  │
    │  ┌────────────────────────┐  │
    │  │  仅截图     Ctrl+9      │  │  ← Secondary button
    │  └────────────────────────┘  │
    │  ┌──────────┐ ┌──────────┐  │
    │  │ 上一步    │ │ 暂停      │  │  ← Two small buttons
    │  │ Ctrl+7   │ │ Ctrl+0    │  │
    │  └──────────┘ └──────────┘  │
    ├──────────────────────────────┤
    │  ⏹ 停止变更  Ctrl+Shift+S    │  ← Stop button (red, full width)
    └──────────────────────────────┘

关键规格:
- 宽度 220px 固定
- 高度由内容驱动，最小 300px
- 默认定位在主屏右边缘垂直居中
- 半透明深色背景 + 12px 圆角
- 始终置顶、无边框、工具窗口（无任务栏入口）
- 支持拖拽移动
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QApplication, QFrame,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QMouseEvent


# ----- 字体 -----

FONT_MONO = "Consolas, Courier New, monospace"
FONT_CN = "Microsoft YaHei, PingFang SC, sans-serif"

# ----- 调色板（清新亮色系） -----

COLOR_BG = "rgba(255, 255, 255, 0.95)"
COLOR_RECORDING = "#ff3b30"
COLOR_PAUSED = "#ff9500"
COLOR_PRIMARY = "#007aff"
COLOR_PRIMARY_HOVER = "#0056cc"
COLOR_SECONDARY_BG = "#f0f4f8"
COLOR_SECONDARY_BORDER = "#d0d7de"
COLOR_STOP_BG = "#ffebee"
COLOR_STOP_BORDER = "#ef9a9a"
COLOR_DIVIDER = "#e8ecf0"
COLOR_TEXT_PRIMARY = "#1a1a2e"
COLOR_TEXT_SECONDARY = "#555555"
COLOR_TEXT_MUTED = "#999999"
COLOR_TEXT_DESC = "#444444"
COLOR_SCROLLBAR = "#cccccc"
COLOR_SHADOW = "rgba(0, 0, 0, 0.08)"


class MiniFloatingWindow(QWidget):
    """浮动操作栏 — 始终置顶，仿 macOS MiniFloatingWindowView 设计。"""

    def __init__(
        self,
        on_capture_and_advance=None,
        on_capture_only=None,
        on_prev=None,
        on_next=None,
        on_toggle_pause=None,
        on_stop=None,
    ):
        super().__init__(None)
        self.setWindowTitle("ChangeSnap")
        self.setFixedWidth(220)
        self.setMinimumHeight(300)
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        # 拖拽状态
        self._drag_pos = None

        # 内部状态
        self._is_recording = True
        self._is_paused = False

        # 回调
        self.on_capture_and_advance = on_capture_and_advance
        self.on_capture_only = on_capture_only
        self.on_prev = on_prev
        self.on_next = on_next
        self.on_toggle_pause = on_toggle_pause
        self.on_stop = on_stop

        self._build_ui()

        # 默认定位到主屏右边缘垂直居中
        self._position_at_right_edge()

    # ==================================================================
    #  界面构建
    # ==================================================================

    def _build_ui(self):
        """构建完整的浮动栏 UI。"""
        # 外层采用透明背景，内部 container 负责圆角+半透明深色背景
        container = QWidget(self)
        container.setObjectName("floatingContainer")
        container.setStyleSheet(f"""
            #floatingContainer {{
                background: {COLOR_BG};
                border-radius: 12px;
                border: 1px solid #e0e0e0;
            }}
        """)
        self._container = container

        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 顶部：录制指示器 + 计时器
        self._build_header(layout)

        # 分割线
        layout.addWidget(self._make_divider())

        # 步骤信息区
        self._build_step_info(layout)

        # 分割线
        layout.addWidget(self._make_divider())

        # 按钮区
        self._build_buttons(layout)

        # 分割线
        layout.addWidget(self._make_divider())

        # 停止按钮
        self._build_stop_button(layout)

        # 最外层布局 — container 填满整个 widget
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

    # ---- 顶部栏 ----

    def _build_header(self, parent_layout):
        """录制指示器圆点 + 状态文字 + 计时器。"""
        header = QHBoxLayout()
        header.setSpacing(6)

        # 红色录制圆点
        self._indicator = QLabel("●")
        self._indicator.setFixedSize(16, 16)
        self._indicator.setStyleSheet(
            f"color: {COLOR_RECORDING}; font-size: 16px;"
        )
        self._indicator.setAlignment(Qt.AlignCenter)
        header.addWidget(self._indicator)

        # 状态文字
        self._status_text = QLabel("录制中")
        self._status_text.setStyleSheet(
            f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px;"
        )
        header.addWidget(self._status_text)

        header.addStretch()

        # 计时器 (HH:MM:SS, 等宽加粗)
        self._timer_label = QLabel("00:00:00")
        self._timer_label.setFont(QFont(FONT_MONO, 13, QFont.Bold))
        self._timer_label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY};")
        self._timer_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header.addWidget(self._timer_label)

        parent_layout.addLayout(header)

    # ---- 步骤信息 ----

    def _build_step_info(self, parent_layout):
        """步骤标签 + 可滚动描述 + 截图计数。"""
        # 步骤标签（如 "▶ 步骤 3/8"）
        self._step_label = QLabel("▶ 步骤 1/1")
        self._step_label.setFont(QFont(FONT_CN, 12, QFont.Bold))
        self._step_label.setStyleSheet(
            f"color: {COLOR_TEXT_PRIMARY}; padding: 2px 0px;"
        )
        self._step_label.setAlignment(Qt.AlignLeft)
        parent_layout.addWidget(self._step_label)

        # 步骤描述（只读、可滚动、自适应高度 50-100px）
        self._desc_edit = QTextEdit()
        self._desc_edit.setReadOnly(True)
        self._desc_edit.setMinimumHeight(50)
        self._desc_edit.setMaximumHeight(100)
        self._desc_edit.setFont(QFont(FONT_CN, 10))
        self._desc_edit.setStyleSheet("""
            QTextEdit {
                background: transparent;
                color: #aaaaaa;
                border: none;
                padding: 2px 0px;
            }
            QScrollBar:vertical {
                width: 4px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #444444;
                border-radius: 2px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        self._desc_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._desc_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._desc_edit.setPlainText("")
        parent_layout.addWidget(self._desc_edit)

        # 截图计数
        self._screenshot_count_label = QLabel("\U0001f4f7 0 张截图")
        self._screenshot_count_label.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; padding: 2px 0px;"
        )
        parent_layout.addWidget(self._screenshot_count_label)

    # ---- 按钮区 ----

    def _build_buttons(self, parent_layout):
        """三个按钮区域：主按钮、次级按钮、并排双按钮。"""
        # ── 截图并前进（主按钮，全宽，蓝色） ──
        self._btn_capture_advance = QPushButton("截图并前进\nCtrl+8")
        self._btn_capture_advance.setMinimumHeight(44)
        self._btn_capture_advance.setStyleSheet(f"""
            QPushButton {{
                background: {COLOR_PRIMARY};
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: bold;
                padding: 8px;
            }}
            QPushButton:hover {{
                background: #0056cc;
            }}
            QPushButton:pressed {{
                background: #004099;
            }}
        """)
        self._btn_capture_advance.clicked.connect(self._on_capture_advance)
        parent_layout.addWidget(self._btn_capture_advance)

        # ── 仅截图（次级按钮，全宽） ──
        self._btn_capture_only = QPushButton("仅截图\nCtrl+9")
        self._btn_capture_only.setMinimumHeight(38)
        self._btn_capture_only.setStyleSheet(f"""
            QPushButton {{
                background: {COLOR_SECONDARY_BG};
                color: {COLOR_TEXT_SECONDARY};
                border: 1px solid {COLOR_SECONDARY_BORDER};
                border-radius: 8px;
                font-size: 12px;
                padding: 6px;
            }}
            QPushButton:hover {{
                background: #e3e8ef;
                border-color: #a0aab4;
            }}
            QPushButton:pressed {{
                background: #d0d7de;
            }}
        """)
        self._btn_capture_only.clicked.connect(self._on_capture_only)
        parent_layout.addWidget(self._btn_capture_only)

        # ── 上一步 + 下一步 + 暂停/恢复（并排三按钮） ──
        small_row = QHBoxLayout()
        small_row.setSpacing(6)

        _small_btn_style = f"""
            QPushButton {{
                background: {COLOR_SECONDARY_BG};
                color: {COLOR_TEXT_SECONDARY};
                border: 1px solid {COLOR_SECONDARY_BORDER};
                border-radius: 8px;
                font-size: 11px;
                padding: 4px 6px;
            }}
            QPushButton:hover {{
                background: #e3e8ef;
                border-color: #a0aab4;
            }}
            QPushButton:pressed {{
                background: #d0d7de;
            }}
        """

        self._btn_prev = QPushButton("上一步")
        self._btn_prev.setMinimumHeight(44)
        self._btn_prev.setStyleSheet(_small_btn_style)
        self._btn_prev.clicked.connect(self._on_prev)
        small_row.addWidget(self._btn_prev)

        self._btn_next = QPushButton("下一步")
        self._btn_next.setMinimumHeight(44)
        self._btn_next.setStyleSheet(_small_btn_style)
        self._btn_next.clicked.connect(self._on_next)
        small_row.addWidget(self._btn_next)

        self._btn_toggle_pause = QPushButton("暂停")
        self._btn_toggle_pause.setMinimumHeight(44)
        self._btn_toggle_pause.setStyleSheet(_small_btn_style)
        self._btn_toggle_pause.clicked.connect(self._on_toggle_pause)
        small_row.addWidget(self._btn_toggle_pause)

        parent_layout.addLayout(small_row)

    # ---- 停止按钮 ----

    def _build_stop_button(self, parent_layout):
        """停止变更：红色全宽按钮。"""
        self._btn_stop = QPushButton("停止变更")
        self._btn_stop.setMinimumHeight(40)
        self._btn_stop.setStyleSheet(f"""
            QPushButton {{
                background: {COLOR_STOP_BG};
                color: #c62828;
                border: 1px solid {COLOR_STOP_BORDER};
                border-radius: 8px;
                font-size: 12px;
                font-weight: bold;
                padding: 8px;
            }}
            QPushButton:hover {{
                background: #ffcdd2;
                border-color: #e57373;
            }}
            QPushButton:pressed {{
                background: #ef9a9a;
            }}
        """)
        self._btn_stop.clicked.connect(self._on_stop)
        parent_layout.addWidget(self._btn_stop)

    # ---- 辅助组件 ----

    @staticmethod
    def _make_divider():
        """创建 1px 分割线。"""
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet(
            f"color: {COLOR_DIVIDER}; margin: 2px 0px; max-height: 1px;"
        )
        return line

    # ==================================================================
    #  定位
    # ==================================================================

    def _position_at_right_edge(self):
        """定位到主屏右边缘（距右 20px）垂直居中。"""
        screen = QApplication.primaryScreen().geometry()
        x = screen.x() + screen.width() - self.width() - 20
        y = screen.y() + (screen.height() - self.minimumHeight()) // 2
        self.move(x, y)
        # 让布局系统计算内容驱动的实际高度
        self.adjustSize()

    # ==================================================================
    #  公开 API
    # ==================================================================

    def update_step(self, text: str):
        """更新步骤标签（如 '步骤 3/8'）。"""
        self._step_label.setText(f"▶ {text}")

    def update_description(self, text: str):
        """更新步骤描述文本。"""
        self._desc_edit.setPlainText(text)

    def update_count(self, current: int, total: int, screenshots: int):
        """更新步骤位置和截图计数。"""
        self._step_label.setText(f"▶ 步骤 {current}/{total}")
        self._screenshot_count_label.setText(
            f"\U0001f4f7 {screenshots} 张截图"
        )

    def update_timer(self, elapsed_seconds: float):
        """更新计时器显示 (HH:MM:SS)。"""
        h = int(elapsed_seconds // 3600)
        m = int((elapsed_seconds % 3600) // 60)
        s = int(elapsed_seconds % 60)
        self._timer_label.setText(f"{h:02d}:{m:02d}:{s:02d}")

    def update_recording_state(self, is_recording: bool, is_paused: bool):
        """更新录制状态指示器。

        参数:
            is_recording: 是否录制中
            is_paused: 是否暂停（仅在 is_recording=True 时有意义）
        """
        self._is_recording = is_recording
        self._is_paused = is_paused

        if is_paused:
            self._indicator.setStyleSheet(
                f"color: {COLOR_PAUSED}; font-size: 16px;"
            )
            self._status_text.setText("已暂停")
            self._btn_toggle_pause.setText("恢复")
        elif is_recording:
            self._indicator.setStyleSheet(
                f"color: {COLOR_RECORDING}; font-size: 16px;"
            )
            self._status_text.setText("录制中")
            self._btn_toggle_pause.setText("暂停")
        else:
            self._indicator.setStyleSheet(
                f"color: #666666; font-size: 16px;"
            )
            self._status_text.setText("未录制")
            self._btn_toggle_pause.setText("暂停")

    # ==================================================================
    #  内部回调转发
    # ==================================================================

    def _on_capture_advance(self):
        if self.on_capture_and_advance:
            self.on_capture_and_advance()

    def _on_capture_only(self):
        if self.on_capture_only:
            self.on_capture_only()

    def _on_prev(self):
        if self.on_prev:
            self.on_prev()

    def _on_next(self):
        if self.on_next:
            self.on_next()

    def _on_toggle_pause(self):
        if self.on_toggle_pause:
            self.on_toggle_pause()

    def _on_stop(self):
        if self.on_stop:
            self.on_stop()

    # ==================================================================
    #  拖拽支持
    # ==================================================================

    def mousePressEvent(self, event: QMouseEvent):
        """记录拖拽起点。"""
        if event.button() == Qt.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        """拖拽移动窗口。"""
        if event.buttons() == Qt.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        """释放拖拽。"""
        self._drag_pos = None
        event.accept()

    # ==================================================================
    #  生命周期
    # ==================================================================

    def closeEvent(self, event):
        """关闭事件 — 什么都不做（由外部管理生命周期）。"""
        pass
