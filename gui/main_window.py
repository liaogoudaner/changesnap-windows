"""ChangeSnap 主窗口 — PySide6 原生 GUI (Windows 适配版)。"""

import os
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QLabel, QPushButton, QTextEdit,
    QFileDialog, QMessageBox, QGroupBox, QScrollArea, QCheckBox,
    QStatusBar, QApplication, QFrame, QGridLayout,
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QPixmap, QFont, QAction, QKeySequence

from models.change_plan import ChangePlan, StepGroup, Step, BasicInfo, TimePersonnel
from models.session import SessionState, SessionStep
from models.screenshot import ScreenshotMeta
from core.config import ConfigManager
from core.plan_parser import PlanParser
from core.session_manager import SessionManager
from core.screenshot_engine import ScreenshotEngine
from core.recording_engine import RecordingEngine
from core.hotkey_manager import HotkeyManager
from core.report_generator import ReportGenerator
from utils.log_utils import get_logger
from gui.tray import SystemTray, create_app_icon

logger = get_logger(__name__)

FONT_MONO = "Consolas, Courier New, monospace"
FONT_CN = "Microsoft YaHei, PingFang SC, sans-serif"


class RecordingThread(QThread):
    """录屏后台线程。"""
    status_update = Signal(dict)

    def __init__(self, engine: RecordingEngine):
        super().__init__()
        self.engine = engine
        self._running = True

    def run(self):
        while self._running:
            if self.engine and self.engine.is_recording:
                self.status_update.emit(self.engine.get_status())
            time.sleep(1)

    def stop(self):
        self._running = False


class FloatingToolbar(QWidget):
    """浮动操作栏 — 始终置顶，无需权限。"""

    def __init__(self, parent=None, on_capture=None, on_capture_only=None,
                 on_prev=None, on_stop=None):
        super().__init__(None)
        self.setWindowTitle("ChangeSnap 操作栏")
        self.setFixedSize(500, 100)
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet("""
            QWidget { background: #1a1a2e; border-radius: 12px; }
            QLabel { color: white; font-size: 12px; }
            QPushButton {
                background: #16213e; color: white; border: 2px solid #0f3460;
                border-radius: 8px; padding: 8px 14px; font-size: 13px; min-width: 70px;
            }
            QPushButton:hover { background: #0f3460; border-color: #e94560; }
            QPushButton:pressed { background: #e94560; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        self._step_label = QLabel("步骤 1/5")
        self._step_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._step_label)

        btn_row = QHBoxLayout()
        btn_cap = QPushButton("📷 截图+前进")
        btn_cap.clicked.connect(on_capture or (lambda: None))
        btn_cap_only = QPushButton("📷 仅截图")
        btn_cap_only.clicked.connect(on_capture_only or (lambda: None))
        btn_prev = QPushButton("⬅ 上一步")
        btn_prev.clicked.connect(on_prev or (lambda: None))
        btn_stop = QPushButton("⏹ 停止")
        btn_stop.setStyleSheet(btn_stop.styleSheet() +
            "QPushButton { background: #7f1d1d; border-color: #dc2626; }"
            "QPushButton:hover { background: #991b1b; }")
        btn_stop.clicked.connect(on_stop or (lambda: None))

        for b in [btn_cap, btn_cap_only, btn_prev, btn_stop]:
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() // 2 - 250, 60)

    def update_step(self, text: str):
        self._step_label.setText(text)

    def closeEvent(self, event):
        pass


class MainWindow(QMainWindow):
    """ChangeSnap 主窗口。"""

    screenshot_captured = Signal(str, object)
    _trigger_capture = Signal(bool)  # 用于将截图调用 marshal 到主线程

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChangeSnap — 变更实施记录与报告生成")
        self.setWindowIcon(create_app_icon())
        self.resize(1280, 800)

        # 轻量组件 — 立即初始化
        self.config = ConfigManager()
        self.plan_parser = PlanParser()
        self.session_manager = SessionManager()

        # 系统托盘
        self._tray = SystemTray()
        self._tray.capture_and_advance.connect(self._toolbar_capture)
        self._tray.capture_only.connect(self._toolbar_capture_only)
        self._tray.previous_step.connect(self._toolbar_prev)
        self._tray.toggle_pause.connect(self._toolbar_toggle_pause)
        self._tray.stop_session.connect(self._toolbar_stop)
        self._tray.show_main_window.connect(self.show)

        # 重量组件 — 延迟初始化
        self._screenshot_engine = None
        self._recording_engine = None
        self._report_generator = None
        self._hotkey_manager = None
        self._rec_thread = None

        # 状态
        self._plan = None
        self._session = None
        self._step_widgets = {}
        self._screenshot_labels = []
        self._floating_toolbar = None

        # 定时器
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(1000)

        # 构建界面
        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self.screenshot_captured.connect(self._on_screenshot_captured)
        self._trigger_capture.connect(self._on_trigger_capture)

    # ---- 延迟加载属性 ----

    @property
    def screenshot_engine(self):
        if self._screenshot_engine is None:
            self._screenshot_engine = ScreenshotEngine()
        return self._screenshot_engine

    @property
    def recording_engine(self):
        if self._recording_engine is None:
            self._recording_engine = RecordingEngine()
        return self._recording_engine

    @property
    def report_generator(self):
        if self._report_generator is None:
            self._report_generator = ReportGenerator()
        return self._report_generator

    @property
    def hotkey_manager(self):
        if self._hotkey_manager is None:
            self._hotkey_manager = HotkeyManager(config_manager=self.config)
        return self._hotkey_manager

    # ---- 界面构建 ----

    def _setup_menu(self):
        """菜单栏。"""
        menu = self.menuBar()

        file_menu = menu.addMenu("文件(&F)")
        load_action = QAction("加载变更方案...", self)
        load_action.setShortcut(QKeySequence("Ctrl+O"))
        load_action.triggered.connect(self._load_plan)
        file_menu.addAction(load_action)
        file_menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        action_menu = menu.addMenu("操作(&A)")
        start_action = QAction("开始变更", self)
        start_action.setShortcut(QKeySequence("Ctrl+R"))
        start_action.triggered.connect(self._start_session)
        action_menu.addAction(start_action)

        stop_action = QAction("停止变更", self)
        stop_action.triggered.connect(self._stop_session)
        action_menu.addAction(stop_action)
        action_menu.addSeparator()

        report_action = QAction("生成总结报告...", self)
        report_action.triggered.connect(self._generate_report)
        action_menu.addAction(report_action)

        help_menu = menu.addMenu("帮助(&H)")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_ui(self):
        """主界面布局。"""
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)

        # 工具栏区域
        toolbar = QHBoxLayout()
        self._btn_load = QPushButton("📂 加载方案")
        self._btn_load.clicked.connect(self._load_plan)
        self._btn_start = QPushButton("▶ 开始变更")
        self._btn_start.clicked.connect(self._start_session)
        self._btn_start.setEnabled(False)
        self._btn_stop = QPushButton("⏹ 停止")
        self._btn_stop.clicked.connect(self._stop_session)
        self._btn_stop.setEnabled(False)
        self._btn_report = QPushButton("📝 生成报告")
        self._btn_report.clicked.connect(self._generate_report)
        self._btn_report.setEnabled(False)

        for btn in [self._btn_load, self._btn_start, self._btn_stop, self._btn_report]:
            btn.setMinimumHeight(36)
            toolbar.addWidget(btn)
        toolbar.addStretch()

        self._lbl_plan_info = QLabel("尚未加载方案")
        self._lbl_plan_info.setStyleSheet("color: #888;")
        toolbar.addWidget(self._lbl_plan_info)
        root.addLayout(toolbar)

        # 主分割区
        splitter = QSplitter(Qt.Horizontal)

        # 左侧：步骤树
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("<b>变更步骤</b>"))
        self._step_tree = QTreeWidget()
        self._step_tree.setHeaderLabels(["步骤", "时间", "实施人"])
        self._step_tree.setColumnWidth(0, 280)
        self._step_tree.setColumnWidth(1, 100)
        self._step_tree.setColumnWidth(2, 80)
        self._step_tree.setMinimumWidth(400)
        self._step_tree.currentItemChanged.connect(self._on_step_selected)
        left_layout.addWidget(self._step_tree)
        splitter.addWidget(left_panel)

        # 右侧：步骤详情 + 截图
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 0, 0, 0)

        # 步骤详情
        detail_group = QGroupBox("步骤详情")
        detail_grid = QGridLayout(detail_group)
        self._lbl_step_title = QLabel("选择一个步骤查看详情")
        self._lbl_step_title.setFont(QFont(FONT_CN, 14, QFont.Bold))
        detail_grid.addWidget(self._lbl_step_title, 0, 0, 1, 2)

        self._lbl_step_time = QLabel("")
        detail_grid.addWidget(self._lbl_step_time, 1, 0)
        self._lbl_step_person = QLabel("")
        detail_grid.addWidget(self._lbl_step_person, 1, 1)

        self._lbl_step_desc = QTextEdit()
        self._lbl_step_desc.setReadOnly(True)
        self._lbl_step_desc.setMinimumHeight(160)
        self._lbl_step_desc.setMaximumHeight(400)
        self._lbl_step_desc.setLineWrapMode(QTextEdit.WidgetWidth)
        detail_grid.addWidget(self._lbl_step_desc, 2, 0, 1, 2)

        self._lbl_supplement = QTextEdit()
        self._lbl_supplement.setPlaceholderText("补充说明（变更后填写）...")
        self._lbl_supplement.setMaximumHeight(60)
        detail_grid.addWidget(QLabel("补充说明:"), 3, 0)
        detail_grid.addWidget(self._lbl_supplement, 3, 1)

        right_layout.addWidget(detail_group)

        # 截图区域
        ss_group = QGroupBox("截图记录")
        ss_layout = QVBoxLayout(ss_group)
        self._ss_scroll = QScrollArea()
        self._ss_scroll.setWidgetResizable(True)
        self._ss_container = QWidget()
        self._ss_container_layout = QHBoxLayout(self._ss_container)
        self._ss_container_layout.setAlignment(Qt.AlignLeft)
        self._ss_scroll.setWidget(self._ss_container)
        self._ss_scroll.setMinimumHeight(200)
        ss_layout.addWidget(self._ss_scroll)
        right_layout.addWidget(ss_group)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

    def _setup_statusbar(self):
        """状态栏。"""
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_recording = QLabel("⚪ 未录制")
        self._status_timer = QLabel("00:00:00")
        self._status_step = QLabel("步骤: -/-")
        self._status_hotkeys = QLabel("快捷键: 停止后生效")

        for w in [self._status_recording, self._status_timer, self._status_step]:
            w.setStyleSheet("padding: 0 8px;")
        self._status_hotkeys.setStyleSheet("padding: 0 8px; color: #666;")

        self._status_bar.addWidget(self._status_recording)
        self._status_bar.addWidget(self._status_timer)
        self._status_bar.addWidget(self._status_step)
        self._status_bar.addPermanentWidget(self._status_hotkeys)

    # ---- 业务逻辑 ----

    def _load_plan(self):
        """加载变更方案文件。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "加载变更方案",
            str(Path.home() / "Downloads"),
            "支持的格式 (*.docx *.xlsx *.xls *.json *.yaml *.yml);;Word 文档 (*.docx);;Excel (*.xlsx *.xls);;JSON (*.json);;YAML (*.yaml *.yml);;所有文件 (*)"
        )
        if not file_path:
            return

        try:
            self._plan = self.plan_parser.parse(Path(file_path))
        except Exception as e:
            QMessageBox.critical(self, "解析失败", str(e))
            return

        self._btn_start.setEnabled(True)
        self._lbl_plan_info.setText(
            f"{self._plan.basic_info.product_name} | "
            f"{self._plan.time_personnel.change_time} | "
            f"{self._plan.total_steps()} 步骤"
        )
        self._populate_step_tree()
        self._status_step.setText(f"步骤: 0/{self._plan.total_steps()}")
        logger.info(f"方案已加载: {self._plan.basic_info.product_name}")

    def _populate_step_tree(self):
        """填充步骤树。使用唯一索引避免跨组 seq 重复。"""
        self._step_tree.clear()
        self._step_widgets.clear()
        self._step_index_map = {}  # index -> (group, step)

        if not self._plan:
            return

        step_index = 0
        for group in self._plan.step_groups:
            group_item = QTreeWidgetItem(self._step_tree)
            group_item.setText(0, f"{group.group_name} ({len(group.steps)}步)")
            group_item.setFlags(group_item.flags() & ~Qt.ItemIsSelectable)
            group_item.setExpanded(True)
            font = QFont(FONT_CN, 11, QFont.Bold)
            group_item.setFont(0, font)

            for step in group.steps:
                step_item = QTreeWidgetItem(group_item)
                step_item.setText(0, f"{step.seq}. {step.description}")
                step_item.setText(1, step.planned_time or '—')
                step_item.setText(2, step.implementer or '—')
                step_item.setData(0, Qt.UserRole, step_index)
                self._step_index_map[step_index] = (group, step)
                group_item.addChild(step_item)
                step_index += 1

    def _on_step_selected(self, current, previous):
        """步骤选中事件。"""
        if not current or not current.data(0, Qt.UserRole) and current.data(0, Qt.UserRole) != 0:
            return
        idx = current.data(0, Qt.UserRole)
        if isinstance(idx, int) and idx in getattr(self, '_step_index_map', {}):
            group, step = self._step_index_map[idx]
            self._lbl_step_title.setText(f"[{group.group_name}] 步骤 {step.seq}")
            self._lbl_step_time.setText(f"计划时间: {step.planned_time or '未指定'}")
            self._lbl_step_person.setText(f"实施: {step.implementer or '-'}  审核: {step.reviewer or '-'}")
            self._lbl_step_desc.setText(step.description)

    def _start_session(self):
        """开始变更。使用 Windows 原生 PySide6 区域选择器。"""
        if not self._plan:
            QMessageBox.warning(self, "提示", "请先加载变更方案")
            return

        # Windows: use our native PySide6 region selector
        from gui.region_selector import RegionSelector
        region = RegionSelector.get_region()
        if region is None:
            return  # User cancelled
        self._capture_region = region
        self._do_start_session()

        # Update tray
        if self._session:
            self._tray.update_state(
                'running',
                step_idx=1,
                total=self._session.total_steps,
            )

    def _do_start_session(self):
        """实际启动变更会话。由 _start_session 调用。"""
        try:
            self._session = self.session_manager.create_session(self._plan)

            if self.recording_engine.is_available:
                fps = self.config.get_setting('recording_fps', 5)
                segment = self.config.get_setting('recording_segment_minutes', 30)
                ok = self.recording_engine.start(self._session.session_id, fps, segment)
                if ok:
                    self.session_manager.init_recording()
                    self._rec_thread = RecordingThread(self.recording_engine)
                    self._rec_thread.status_update.connect(self._on_recording_status)
                    self._rec_thread.start()

            self._start_hotkeys()

            self._floating_toolbar = FloatingToolbar(
                on_capture=self._toolbar_capture,
                on_capture_only=self._toolbar_capture_only,
                on_prev=self._toolbar_prev,
                on_stop=self._toolbar_stop,
            )
            self._floating_toolbar.update_step(f"步骤 1/{self._session.total_steps}")
            self._floating_toolbar.show()

            self._btn_start.setEnabled(False)
            self._btn_stop.setEnabled(True)
            self._btn_load.setEnabled(False)
            self._status_recording.setText("🔴 录制中")
            self._status_step.setText(f"步骤: 1/{self._session.total_steps}")
            self._lbl_supplement.setReadOnly(False)

            logger.info(f"变更已开始: {self._session.session_id}")

        except Exception as e:
            logger.error(f"启动变更失败: {e}", exc_info=True)
            QMessageBox.critical(self, "启动失败", f"无法开始变更:\n{e}")

    def _stop_session(self):
        """停止变更。"""
        if not self._session:
            return

        # 关闭浮动操作栏
        if self._floating_toolbar:
            self._floating_toolbar.hide()
            self._floating_toolbar.deleteLater()
            self._floating_toolbar = None

        # 停止录屏
        if self._rec_thread:
            self._rec_thread.stop()
            self._rec_thread.wait(3000)
            self._rec_thread = None

        self.recording_engine.stop()

        # 停止热键
        if self.hotkey_manager:
            self.hotkey_manager.unregister_all()

        self.session_manager.finalize_recording()
        self.session_manager.set_reviewing()
        self.session_manager.save()

        self._btn_stop.setEnabled(False)
        self._btn_report.setEnabled(True)
        self._status_recording.setText("⚪ 已停止")
        self._status_timer.setText(self._format_elapsed(0))

        # 刷新截图展示
        self._refresh_screenshots()

        # Update tray
        self._tray.update_state('reviewing')

        logger.info(f"变更已停止: {self._session.session_id}")

    def _generate_report(self):
        """生成总结报告。"""
        if not self._session:
            return

        # 保存补充说明
        self._save_supplement()

        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存总结报告",
            str(Path.home() / "Downloads" / f"{self._plan.basic_info.product_name}变更总结报告.docx"),
            "Word 文档 (*.docx)"
        )
        if not file_path:
            return

        result = self.report_generator.generate(self._session, Path(file_path))
        if result['success']:
            QMessageBox.information(
                self, "生成成功",
                f"总结报告已生成:\n{result['filepath']}\n大小: {result['file_size'] / 1024:.0f}KB"
            )
        else:
            QMessageBox.critical(self, "生成失败", result.get('error', '未知错误'))

    def _save_supplement(self):
        """保存补充说明到会话。"""
        if not self._session or not self._step_tree:
            return
        item = self._step_tree.currentItem()
        if item and item.data(0, Qt.UserRole) is not None:
            idx = item.data(0, Qt.UserRole)
            text = self._lbl_supplement.toPlainText().strip()
            if text and isinstance(idx, int) and idx in getattr(self, '_step_index_map', {}):
                group, step = self._step_index_map[idx]
                self.session_manager.update_step_supplement_by_seq(step.seq, text)

    # ---- 截图处理 ----

    def _make_screenshot_callback(self, auto_advance: bool = True):
        """生成截图回调（供热键使用）。

        通过发射 _trigger_capture 信号将实际 mss 截图调用 marshal 到主线程，
        避免在 pynput 的监听器线程中直接调用 mss（其内部使用 ScreenCaptureKit，
        必须在主线程运行）。
        """
        def callback():
            self._trigger_capture.emit(auto_advance)
        return callback

    def _on_trigger_capture(self, auto_advance: bool):
        """在主线程执行实际截图（_trigger_capture 信号的槽）。"""
        if not self._session or self._session.status not in ('running', 'paused'):
            return

        step_id = self._session.current_step_id
        step = self._session.get_current_step()
        seq = step.seq if step else 0
        region = getattr(self, '_capture_region', None)

        meta = self.screenshot_engine.capture(
            session_id=self._session.session_id,
            step_id=step_id or 'orphan',
            seq=seq,
            region=region,
        )

        if meta:
            self.session_manager.add_screenshot(step_id or 'orphan', meta)
            self.screenshot_captured.emit(step_id or 'orphan', meta)
            logger.info(f"截图完成: {meta.filename}")

            if auto_advance and step_id:
                self.session_manager.advance_step()

    def _toolbar_capture(self):
        """浮动栏：截图并前进。"""
        cb = self._make_screenshot_callback(True)
        cb()
        if self._session:
            idx = self._session.current_step_index + 1
            total = self._session.total_steps
            self._status_step.setText(f"步骤: {idx}/{total}")
            if self._floating_toolbar:
                self._floating_toolbar.update_step(f"步骤 {idx}/{total}")
            self._refresh_screenshots()

    def _toolbar_capture_only(self):
        """浮动栏：仅截图。"""
        cb = self._make_screenshot_callback(False)
        cb()
        self._refresh_screenshots()

    def _toolbar_prev(self):
        """浮动栏：上一步。"""
        if self._session:
            self.session_manager.prev_step()
            idx = self._session.current_step_index + 1
            total = self._session.total_steps
            self._status_step.setText(f"步骤: {idx}/{total}")
            if self._floating_toolbar:
                self._floating_toolbar.update_step(f"步骤 {idx}/{total}")

    def _toolbar_stop(self):
        """浮动栏：停止。"""
        self._stop_session()

    def _toolbar_toggle_pause(self):
        """暂停/恢复录屏（托盘菜单）。"""
        re = self.recording_engine
        if re.is_paused:
            re.resume()
            self.session_manager.resume_recording()
            self._status_recording.setText("🔴 录制中")
            if self._session:
                self._tray.update_state(
                    'running',
                    step_idx=self._session.current_step_index + 1,
                    total=self._session.total_steps,
                    elapsed=re.elapsed_seconds,
                )
        elif re.is_recording:
            re.pause()
            self.session_manager.pause_recording()
            self._status_recording.setText("⏸ 已暂停")
            if self._session:
                self._tray.update_state(
                    'paused',
                    step_idx=self._session.current_step_index + 1,
                    total=self._session.total_steps,
                )

    def _on_screenshot_captured(self, step_id: str, meta: ScreenshotMeta):
        """截图完成后的 UI 更新。"""
        self._refresh_screenshots()
        if self._session:
            idx = self._session.current_step_index + 1
            total = self._session.total_steps
            self._status_step.setText(f"步骤: {idx}/{total}")
            if self._floating_toolbar:
                self._floating_toolbar.update_step(f"步骤 {idx}/{total}")

    def _refresh_screenshots(self):
        """刷新截图展示区。"""
        for label in self._screenshot_labels:
            label.setParent(None)
        self._screenshot_labels.clear()

        if not self._session:
            return

        current = self._step_tree.currentItem()
        if not current or current.data(0, Qt.UserRole) is None:
            return

        idx = current.data(0, Qt.UserRole)
        if not isinstance(idx, int) or idx not in getattr(self, '_step_index_map', {}):
            return
        group, ref_step = self._step_index_map[idx]

        all_steps = self._session.get_all_steps()
        screenshots = []
        for step in all_steps:
            if step.seq == ref_step.seq and step.description[:30] == ref_step.description[:30]:
                screenshots = step.screenshots
                break

        for ss in screenshots:
            if ss.status == 'deleted':
                continue

            frame = QFrame()
            frame.setFrameStyle(QFrame.StyledPanel)
            frame.setMaximumWidth(320)
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(4, 4, 4, 4)

            if os.path.exists(ss.filepath):
                pixmap = QPixmap(ss.filepath)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(300, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    img_label = QLabel()
                    img_label.setPixmap(scaled)
                    img_label.setAlignment(Qt.AlignCenter)
                    layout.addWidget(img_label)

            # 时间戳
            ts = ss.timestamp
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    ts_str = dt.strftime('%H:%M:%S')
                except (ValueError, TypeError):
                    ts_str = str(ts)
                layout.addWidget(QLabel(f"🕐 {ts_str}"))

            # 保留勾选
            cb = QCheckBox("保留")
            cb.setChecked(ss.status in ('active', 'kept'))
            cb.toggled.connect(lambda checked, s=ss: self.session_manager.set_screenshot_kept(s.screenshot_id, checked))
            layout.addWidget(cb)

            self._ss_container_layout.addWidget(frame)
            self._screenshot_labels.append(frame)

    # ---- 热键管理 ----

    def _start_hotkeys(self):
        """启动全局快捷键（直接尝试，失败也不崩）。"""
        hm = self.hotkey_manager
        if not hm or not hm.is_available:
            self._status_hotkeys.setText("快捷键: 不可用 (pynput)")
            return

        hotkey_map = {
            'screenshot_and_advance': (self.config.get_hotkey('screenshot_and_advance'), self._make_screenshot_callback(True)),
            'screenshot_only': (self.config.get_hotkey('screenshot_only'), self._make_screenshot_callback(False)),
            'prev_step': (self.config.get_hotkey('prev_step'), self._make_prev_step_callback()),
            'toggle_recording': (self.config.get_hotkey('toggle_recording'), self._make_toggle_recording_callback()),
            'stop_session': (self.config.get_hotkey('stop_session'), self._make_stop_callback()),
        }

        try:
            hm.start(hotkey_map)
            QTimer.singleShot(2000, self._check_hotkey_health)
        except Exception as e:
            logger.warning(f"热键启动失败: {e}")
            self._status_hotkeys.setText("快捷键: 启动失败 (需辅助功能权限)")

    def _check_hotkey_health(self):
        hm = self.hotkey_manager
        if hm and hm.is_listening:
            self._status_hotkeys.setText("Ctrl+8:截图 | Ctrl+9:仅截图 | Ctrl+7:上一步 | Ctrl+0:暂停 | Ctrl+Shift+S:停止")
        elif hm and hm.is_available:
            self._status_hotkeys.setText("快捷键: 未启动 (系统设置→隐私→辅助功能 添加 ChangeSnap)")
        else:
            self._status_hotkeys.setText("快捷键: 不可用")

    def _make_prev_step_callback(self):
        def cb():
            if self._session:
                self.session_manager.prev_step()
        return cb

    def _make_toggle_recording_callback(self):
        def cb():
            re = self.recording_engine
            if re.is_paused:
                re.resume()
                self.session_manager.resume_recording()
            elif re.is_recording:
                re.pause()
                self.session_manager.pause_recording()
        return cb

    def _make_stop_callback(self):
        def cb():
            if self._session and self._session.status == 'running':
                self.recording_engine.stop()
                self.session_manager.finalize_recording()
                self.session_manager.set_reviewing()
                self._btn_stop.setEnabled(False)
                self._btn_report.setEnabled(True)
                self._status_recording.setText("⚪ 已停止")
        return cb

    # ---- 状态刷新 ----

    def _refresh_status(self):
        """每秒刷新状态栏。"""
        re = self.recording_engine
        if re and re.is_recording:
            elapsed = re.elapsed_seconds
            self._status_timer.setText(self._format_elapsed(elapsed))
            # Update tray with current elapsed time and step
            if self._session:
                idx = self._session.current_step_index + 1
                self._tray.update_state(
                    'running',
                    step_idx=idx,
                    total=self._session.total_steps,
                    elapsed=int(elapsed),
                )
        elif re and re.is_paused:
            # Update tray to show paused state
            if self._session:
                idx = self._session.current_step_index + 1
                self._tray.update_state(
                    'paused',
                    step_idx=idx,
                    total=self._session.total_steps,
                )
        elif not re or not re.is_recording:
            pass  # 保持停止时显示

    def _on_recording_status(self, status: dict):
        """录屏状态更新回调。"""
        if status.get('is_recording'):
            self._status_recording.setText("🔴 录制中")
        elif status.get('is_paused'):
            self._status_recording.setText("⏸ 已暂停")

    def _format_elapsed(self, seconds: float) -> str:
        """格式化时间。"""
        if seconds <= 0:
            return "00:00:00"
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _show_about(self):
        """关于对话框。"""
        QMessageBox.about(
            self, "关于 ChangeSnap",
            "ChangeSnap v1.0\n\n变更实施记录与报告生成工具\n\n"
            "加载 Word/Excel 变更方案 → 录制屏幕 + 快捷键截图 → 一键生成总结报告\n\n"
            "跨平台: macOS / Windows"
        )

    # ---- 生命周期 ----

    def closeEvent(self, event):
        """窗口关闭。"""
        if self._rec_thread:
            self._rec_thread.stop()
            self._rec_thread.wait(2000)

        if self.recording_engine:
            self.recording_engine.stop()

        if self.hotkey_manager:
            self.hotkey_manager.unregister_all()

        if self.session_manager and self._session:
            self.session_manager.save()

        event.accept()
