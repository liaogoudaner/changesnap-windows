"""ChangeSnap 主窗口 — PySide6 原生 GUI (Windows 适配版)。"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QLabel, QPushButton, QTextEdit,
    QFileDialog, QMessageBox, QGroupBox, QScrollArea, QCheckBox,
    QStatusBar, QApplication, QFrame, QGridLayout, QLineEdit,
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QPixmap, QFont, QAction, QKeySequence

from models.change_plan import ChangePlan, StepGroup, Step, BasicInfo, TimePersonnel
from models.session import SessionState, SessionStep, SessionSummary
from models.screenshot import ScreenshotMeta
from core.config import ConfigManager
from core.plan_parser import PlanParser
from core.session_manager import SessionManager
from core.screenshot_engine import ScreenshotEngine
from core.recording_engine import RecordingEngine
from core.hotkey_manager import HotkeyManager
from core.report_generator import ReportGenerator
from utils.log_utils import get_logger
from utils.file_utils import safe_filename, get_plan_output_dir
from gui.tray import SystemTray, create_app_icon
from gui.floating_toolbar import MiniFloatingWindow

# Windows native event handling for WM_HOTKEY
if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes

    class _WinMsg(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
        ]

    WM_HOTKEY = 0x0312
else:
    WM_HOTKEY = None

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
        self._plan_filepath = None  # 方案源文件路径，用于命名输出目录
        self._session = None
        self._step_widgets = {}
        self._screenshot_labels = []
        self._floating_toolbar = None

        # 审核模式
        self._review_mode = False
        self._review_widgets = {}

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

        # 检查未完成的会话（延迟执行，等待 UI 完全初始化）
        QTimer.singleShot(500, self._check_incomplete_sessions)

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
        self._btn_load = QPushButton("\U0001f4c2 加载方案")
        self._btn_load.clicked.connect(self._load_plan)
        self._btn_start = QPushButton("▶ 开始变更")
        self._btn_start.clicked.connect(self._start_session)
        self._btn_start.setEnabled(False)
        self._btn_stop = QPushButton("⏹ 停止")
        self._btn_stop.clicked.connect(self._stop_session)
        self._btn_stop.setEnabled(False)
        self._btn_report = QPushButton("\U0001f4dd 生成报告")
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

        # 右侧：步骤详情 + 截图（会被审核面板替换）
        self._right_panel = QWidget()
        self._right_layout = QVBoxLayout(self._right_panel)
        self._right_layout.setContentsMargins(8, 0, 0, 0)

        # 步骤详情
        self._detail_group = QGroupBox("步骤详情")
        detail_grid = QGridLayout(self._detail_group)
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

        self._right_layout.addWidget(self._detail_group)

        # 截图区域
        self._ss_group = QGroupBox("截图记录")
        ss_layout = QVBoxLayout(self._ss_group)
        self._ss_scroll = QScrollArea()
        self._ss_scroll.setWidgetResizable(True)
        self._ss_container = QWidget()
        self._ss_container_layout = QHBoxLayout(self._ss_container)
        self._ss_container_layout.setAlignment(Qt.AlignLeft)
        self._ss_scroll.setWidget(self._ss_container)
        self._ss_scroll.setMinimumHeight(200)
        ss_layout.addWidget(self._ss_scroll)
        self._right_layout.addWidget(self._ss_group)

        splitter.addWidget(self._right_panel)
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
            self._plan_filepath = Path(file_path)
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

    def _get_plan_base_name(self) -> str:
        """获取方案基础名称（用于目录和文件命名）。

        PlanBaseName = source file name without extension, or product name sanitized.
        与 macOS 版本保持一致：
        - outputs/<planBaseName>/recording_XXX.mp4
        - outputs/<planBaseName>/[总结报告]<planBaseName>.docx
        """
        if self._plan_filepath:
            return self._plan_filepath.stem
        if self._plan and self._plan.basic_info.product_name:
            return self._plan.basic_info.product_name
        return "未知方案"

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
                ok = self.recording_engine.start(
                    self._session.session_id,
                    fps,
                    segment,
                    region=getattr(self, '_capture_region', None),
                    plan_name=self._get_plan_base_name(),
                )
                if ok:
                    self.session_manager.init_recording()
                    self._rec_thread = RecordingThread(self.recording_engine)
                    self._rec_thread.status_update.connect(self._on_recording_status)
                    self._rec_thread.start()

            self._start_hotkeys()

            # 创建浮动操作栏 — MiniFloatingWindow (220px 垂直面板)
            current_step = self._session.get_current_step()
            total = self._session.total_steps
            all_steps = self._session.get_all_steps()
            total_ss = sum(len(s.screenshots) for s in all_steps)

            self._floating_toolbar = MiniFloatingWindow(
                on_capture_and_advance=self._toolbar_capture,
                on_capture_only=self._toolbar_capture_only,
                on_prev=self._toolbar_prev,
                on_toggle_pause=self._toolbar_toggle_pause,
                on_stop=self._toolbar_stop,
            )
            self._floating_toolbar.update_step(f"步骤 1/{total}")
            self._floating_toolbar.update_description(
                current_step.description if current_step else ""
            )
            self._floating_toolbar.update_count(1, total, total_ss)
            self._floating_toolbar.update_timer(0.0)
            self._floating_toolbar.update_recording_state(True, False)
            self._floating_toolbar.show()

            self._btn_start.setEnabled(False)
            self._btn_stop.setEnabled(True)
            self._btn_load.setEnabled(False)
            self._status_recording.setText("\U0001f534 录制中")
            self._status_step.setText(f"步骤: 1/{total}")
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

        # 进入审核面板模式
        self._show_review_panel()

    def _generate_report(self):
        """生成总结报告。

        输出路径与 macOS 版本保持一致：
            outputs/<planBaseName>/[总结报告]<planBaseName>.docx
        """
        if not self._session:
            return

        # 保存审核面板中的总结数据
        if self._review_mode:
            self._save_summary()

        # 保存补充说明
        self._save_supplement()

        # 与 macOS 版本一致的目录和文件命名
        plan_name = self._get_plan_base_name()
        safe_name = safe_filename(plan_name)
        output_dir = get_plan_output_dir(plan_name)  # 创建 outputs/<planName>/
        default_path = str(output_dir / f"[总结报告]{safe_name}.docx")

        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存总结报告",
            default_path,
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
                current_step = self._session.get_current_step()
                all_steps = self._session.get_all_steps()
                total_ss = sum(len(s.screenshots) for s in all_steps)
                self._floating_toolbar.update_count(idx, total, total_ss)
                self._floating_toolbar.update_step(f"步骤 {idx}/{total}")
                if current_step:
                    self._floating_toolbar.update_description(current_step.description)
            self._refresh_screenshots()

    def _toolbar_capture_only(self):
        """浮动栏：仅截图。"""
        cb = self._make_screenshot_callback(False)
        cb()
        if self._floating_toolbar and self._session:
            all_steps = self._session.get_all_steps()
            total_ss = sum(len(s.screenshots) for s in all_steps)
            idx = self._session.current_step_index + 1
            total = self._session.total_steps
            self._floating_toolbar.update_count(idx, total, total_ss)
        self._refresh_screenshots()

    def _toolbar_prev(self):
        """浮动栏：上一步。"""
        if self._session:
            self.session_manager.prev_step()
            idx = self._session.current_step_index + 1
            total = self._session.total_steps
            self._status_step.setText(f"步骤: {idx}/{total}")
            if self._floating_toolbar:
                current_step = self._session.get_current_step()
                all_steps = self._session.get_all_steps()
                total_ss = sum(len(s.screenshots) for s in all_steps)
                self._floating_toolbar.update_count(idx, total, total_ss)
                self._floating_toolbar.update_step(f"步骤 {idx}/{total}")
                if current_step:
                    self._floating_toolbar.update_description(current_step.description)

    def _toolbar_stop(self):
        """浮动栏：停止。"""
        self._stop_session()

    def _toolbar_toggle_pause(self):
        """暂停/恢复录屏。"""
        re = self.recording_engine
        if re.is_paused:
            re.resume()
            self.session_manager.resume_recording()
            self._status_recording.setText("\U0001f534 录制中")
            if self._session:
                self._tray.update_state(
                    'running',
                    step_idx=self._session.current_step_index + 1,
                    total=self._session.total_steps,
                    elapsed=re.elapsed_seconds,
                )
            if self._floating_toolbar:
                self._floating_toolbar.update_recording_state(True, False)
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
            if self._floating_toolbar:
                self._floating_toolbar.update_recording_state(True, True)

    def _on_screenshot_captured(self, step_id: str, meta: ScreenshotMeta):
        """截图完成后的 UI 更新。"""
        self._refresh_screenshots()
        if self._session:
            idx = self._session.current_step_index + 1
            total = self._session.total_steps
            self._status_step.setText(f"步骤: {idx}/{total}")
            if self._floating_toolbar:
                all_steps = self._session.get_all_steps()
                total_ss = sum(len(s.screenshots) for s in all_steps)
                self._floating_toolbar.update_count(idx, total, total_ss)
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
                layout.addWidget(QLabel(f"\U0001f550 {ts_str}"))

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
        """每秒刷新状态栏和浮动栏的计时器。"""
        re = self.recording_engine
        if re and re.is_recording:
            elapsed = re.elapsed_seconds
            self._status_timer.setText(self._format_elapsed(elapsed))
            # 同步浮动栏计时器
            if self._floating_toolbar:
                self._floating_toolbar.update_timer(elapsed)
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
            self._status_recording.setText("\U0001f534 录制中")
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

    # ---- 会话崩溃恢复（Task 1） ----

    def _check_incomplete_sessions(self):
        """启动时检测未完成会话，提供恢复/放弃/忽略选项。

        调用 SessionManager.find_incomplete_sessions() 扫描 ~/.session/ 下所有
        未完成会话，展示最近一个会话的恢复对话框。
        """
        sessions = self.session_manager.find_incomplete_sessions()
        if not sessions:
            logger.debug("未发现未完成的会话")
            return

        recent = sessions[0]
        product_name = recent.get('product_name', '未知方案')
        updated_at = recent.get('updated_at', '未知时间')

        reply = QMessageBox.question(
            self,
            "检测到未完成的会话",
            f"产品: {product_name}\n时间: {updated_at}\n\n是否恢复？",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
        )

        if reply == QMessageBox.Yes:
            # "恢复" — 加载会话并恢复状态
            session = self.session_manager.load_session(recent['session_id'])
            if session:
                self._session = session

                # 从会话数据恢复方案对象
                parsed = session.plan_file.get('parsed')
                if isinstance(parsed, dict):
                    self._plan = ChangePlan.from_dict(parsed)
                    self._populate_step_tree()
                    self._lbl_plan_info.setText(
                        f"{self._plan.basic_info.product_name} | "
                        f"{self._plan.time_personnel.change_time} | "
                        f"{self._plan.total_steps()} 步骤"
                    )

                self._btn_start.setEnabled(False)
                self._btn_load.setEnabled(False)

                if session.status in ('running', 'paused'):
                    # 恢复录制/暂停模式
                    self._btn_stop.setEnabled(True)
                    self._lbl_supplement.setReadOnly(False)
                    self._status_recording.setText(
                        "\U0001f534 录制中" if session.status == 'running' else "⏸ 已暂停"
                    )
                    self._start_hotkeys()
                elif session.status == 'reviewing':
                    # 恢复审核模式
                    self._btn_report.setEnabled(True)
                    self._show_review_panel()

                self._refresh_screenshots()
                logger.info(f"已恢复会话: {recent['session_id']} ({session.status})")

        elif reply == QMessageBox.No:
            # "放弃" — 标记为已放弃
            self.session_manager.mark_abandoned(recent['session_id'])
            logger.info(f"已放弃会话: {recent['session_id']}")
        # "忽略" (Cancel) — 不执行任何操作

    # ---- 审核面板（Task 2） ----

    def _show_review_panel(self):
        """将右侧面板切换为审核模式，替换步骤详情和截图区域。

        展示：
        1. 变更概况（统计卡片 + 概况总结 + 总结改进 + 人员字段）
        2. 步骤审核（各步骤缩略图，点击切换保留/取消保留）
        3. "生成报告" 按钮
        """
        if self._review_mode:
            return
        self._review_mode = True

        # 从布局中移除旧面板组件
        self._right_layout.removeWidget(self._detail_group)
        self._right_layout.removeWidget(self._ss_group)
        self._detail_group.hide()
        self._ss_group.hide()

        # 创建审核滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)

        # 1. 汇总卡片
        summary_group = self._build_summary_section()
        content_layout.addWidget(summary_group)

        # 2. 步骤审核区域
        step_section = self._build_step_review_section()
        content_layout.addWidget(step_section)
        self._review_widgets['step_section'] = step_section

        # 3. "生成报告" 按钮
        generate_btn = QPushButton("\U0001f4dd 生成报告")
        generate_btn.setMinimumHeight(40)
        generate_btn.setStyleSheet("""
            QPushButton {
                background: #2563eb; color: white; border: none;
                border-radius: 8px; font-size: 15px; font-weight: bold;
                padding: 10px;
            }
            QPushButton:hover { background: #1d4ed8; }
            QPushButton:pressed { background: #1e40af; }
        """)
        generate_btn.clicked.connect(self._on_review_generate)
        content_layout.addWidget(generate_btn)

        content_layout.addStretch()

        scroll.setWidget(content)
        self._right_layout.addWidget(scroll)

        self._review_widgets['scroll'] = scroll
        self._review_widgets['content'] = content
        self._review_widgets['generate_btn'] = generate_btn

        logger.info("已切换到审核面板")

    def _on_review_generate(self):
        """审核面板 "生成报告" 按钮回调。

        先保存总结数据，再调用 _generate_report()。
        """
        self._save_summary()
        self._generate_report()

    def _build_summary_section(self) -> QGroupBox:
        """构建审核面板的变更概况区域。

        包含：
        - 统计卡片（产品名称、变更时间、实际耗时、截图数量）
        - 概况总结 QTextEdit（可编辑，textChanged 自动保存）
        - 总结改进 QTextEdit（可编辑，textChanged 自动保存）
        - 人员字段（实施人/测试人/审核人 QLineEdit，textChanged 自动保存）
        """
        group = QGroupBox("变更概况")
        layout = QVBoxLayout(group)

        # ---- 统计卡片 ----
        stats_text = self._build_stats_text()
        stats_label = QLabel(stats_text)
        stats_label.setStyleSheet("""
            font-size: 13px; padding: 12px; background: #f0f4ff;
            border-radius: 6px; border: 1px solid #dbeafe;
            line-height: 1.6;
        """)
        stats_label.setWordWrap(True)
        layout.addWidget(stats_label)

        # ---- 概况总结 ----
        layout.addWidget(QLabel("概况总结:"))
        overview_edit = QTextEdit()
        overview_edit.setPlaceholderText("输入变更概况总结（可选）...")
        overview_edit.setMaximumHeight(120)
        if self._session and self._session.summary and self._session.summary.overview:
            overview_edit.setText(self._session.summary.overview)
        overview_edit.textChanged.connect(self._save_summary)
        layout.addWidget(overview_edit)
        self._review_widgets['overview_edit'] = overview_edit

        # ---- 总结改进 ----
        layout.addWidget(QLabel("总结改进:"))
        improvements_edit = QTextEdit()
        improvements_edit.setPlaceholderText("输入总结和改进建议（可选）...")
        improvements_edit.setMaximumHeight(120)
        if self._session and self._session.summary and self._session.summary.improvements:
            improvements_edit.setText(self._session.summary.improvements)
        improvements_edit.textChanged.connect(self._save_summary)
        layout.addWidget(improvements_edit)
        self._review_widgets['improvements_edit'] = improvements_edit

        # ---- 人员字段 ----
        person_grid = QGridLayout()
        person_grid.setSpacing(6)

        person_grid.addWidget(QLabel("实施人:"), 0, 0)
        implementer_edit = QLineEdit()
        implementer_edit.setPlaceholderText("实施人姓名")
        if self._session and self._session.summary and self._session.summary.actual_implementer:
            implementer_edit.setText(self._session.summary.actual_implementer)
        implementer_edit.textChanged.connect(self._save_summary)
        person_grid.addWidget(implementer_edit, 0, 1)
        self._review_widgets['implementer_edit'] = implementer_edit

        person_grid.addWidget(QLabel("测试人:"), 1, 0)
        tester_edit = QLineEdit()
        tester_edit.setPlaceholderText("测试人姓名")
        if self._session and self._session.summary and self._session.summary.actual_tester:
            tester_edit.setText(self._session.summary.actual_tester)
        tester_edit.textChanged.connect(self._save_summary)
        person_grid.addWidget(tester_edit, 1, 1)
        self._review_widgets['tester_edit'] = tester_edit

        person_grid.addWidget(QLabel("审核人:"), 2, 0)
        reviewer_edit = QLineEdit()
        reviewer_edit.setPlaceholderText("审核人姓名")
        if self._session and self._session.summary and self._session.summary.actual_reviewer:
            reviewer_edit.setText(self._session.summary.actual_reviewer)
        reviewer_edit.textChanged.connect(self._save_summary)
        person_grid.addWidget(reviewer_edit, 2, 1)
        self._review_widgets['reviewer_edit'] = reviewer_edit

        layout.addLayout(person_grid)

        return group

    def _build_stats_text(self) -> str:
        """构建统计信息文本。

        从 _plan 和 _session 中读取产品名称、变更时间、实际耗时、截图数量。
        """
        if not self._session or not self._plan:
            return "无会话数据"

        lines = []
        lines.append(f"产品名称: {self._plan.basic_info.product_name}")
        lines.append(f"变更时间: {self._plan.time_personnel.change_time}")

        # 计算实际耗时
        if self._session.start_time:
            try:
                start = datetime.fromisoformat(self._session.start_time)
                if self._session.end_time:
                    end = datetime.fromisoformat(self._session.end_time)
                    duration_min = int((end - start).total_seconds() / 60)
                    lines.append(f"实际耗时: {duration_min} 分钟")
                else:
                    lines.append("实际耗时: 进行中")
            except (ValueError, TypeError):
                pass

        screenshot_count = self._session.get_screenshot_count()
        lines.append(f"截图数量: {screenshot_count}")

        return "\n".join(lines)

    def _build_step_review_section(self) -> QGroupBox:
        """构建步骤审核区域。

        每个步骤显示标题行和截图缩略图行。
        缩略图点击可切换保留/取消保留状态。
        """
        group = QGroupBox("步骤审核")
        layout = QVBoxLayout(group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(250)

        content = QWidget()
        self._review_widgets['step_scroll_layout'] = QVBoxLayout(content)

        self._populate_step_frames()

        scroll.setWidget(content)
        layout.addWidget(scroll)

        return group

    def _populate_step_frames(self):
        """填充步骤框架到步骤滚动布局。

        每个步骤生成一个 QFrame，包含：
        - 状态 emoji + 步骤标题
        - 截图缩略图行（120x80，点击切换保留/取消保留）
        - 无截图时显示橙色警告提示
        """
        step_layout = self._review_widgets.get('step_scroll_layout')
        if not step_layout:
            return

        # 清除现有子 widget
        while step_layout.count():
            item = step_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._session:
            step_layout.addWidget(QLabel("无会话数据"))
            return

        all_steps = self._session.get_all_steps()
        for step in all_steps:
            frame = QFrame()
            frame.setFrameStyle(QFrame.StyledPanel)
            frame.setStyleSheet("QFrame { margin: 2px 0; }")
            step_vlayout = QVBoxLayout(frame)
            step_vlayout.setContentsMargins(8, 6, 8, 6)

            # 步骤标题行
            status_emoji = {
                'completed': '✅', 'active': '▶️',
                'pending': '⏳', 'skipped': '⏭️',
            }.get(step.status, '⏳')
            header = QLabel(f"{status_emoji} 步骤 {step.seq}: {step.description}")
            header.setWordWrap(True)
            header.setStyleSheet("font-weight: bold; font-size: 12px; padding: 2px 0;")
            step_vlayout.addWidget(header)

            # 截图缩略图行
            ss_row = QHBoxLayout()
            ss_row.setSpacing(6)

            kept_screenshots = [ss for ss in step.screenshots if ss.status in ('active', 'kept')]

            if not kept_screenshots:
                warning_label = QLabel("⚠️ 该步骤未截图")
                warning_label.setStyleSheet("color: #e67e22; font-size: 11px; padding: 4px;")
                ss_row.addWidget(warning_label)
            else:
                for ss in kept_screenshots:
                    thumb = self._create_screenshot_thumb(ss)
                    ss_row.addWidget(thumb)

            ss_row.addStretch()
            step_vlayout.addLayout(ss_row)

            step_layout.addWidget(frame)

        step_layout.addStretch()

    def _create_screenshot_thumb(self, screenshot: ScreenshotMeta) -> QFrame:
        """创建可点击的截图缩略图（140x100）。

        kept 状态: 绿色边框 + 浅绿背景 + "✓ 已保留"标签
        active 状态: 浅灰色边框 + 白色背景
        点击触发 _toggle_screenshot_kept 切换状态。
        """
        frame = QFrame()
        is_kept = screenshot.status == 'kept'
        frame.setFixedSize(140, 100)
        frame.setCursor(Qt.PointingHandCursor)

        if is_kept:
            frame.setStyleSheet("""
                QFrame {
                    border: 2px solid #22c55e;
                    border-radius: 4px;
                    background: #f0fdf4;
                }
                QFrame:hover { border-color: #16a34a; }
            """)
        else:
            frame.setStyleSheet("""
                QFrame {
                    border: 1px solid #d1d5db;
                    border-radius: 4px;
                    background: white;
                }
                QFrame:hover { border-color: #9ca3af; }
            """)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)

        # 缩略图
        img_label = QLabel()
        img_label.setFixedHeight(75)
        img_label.setAlignment(Qt.AlignCenter)
        if os.path.exists(screenshot.filepath):
            pixmap = QPixmap(screenshot.filepath)
            if not pixmap.isNull():
                scaled = pixmap.scaled(130, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                img_label.setPixmap(scaled)
        layout.addWidget(img_label)

        # 底部栏: 时间戳 + 保留状态
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)

        # 时间戳
        ts = screenshot.timestamp or ''
        try:
            dt = datetime.fromisoformat(ts)
            ts_str = dt.strftime('%H:%M:%S')
        except (ValueError, TypeError):
            ts_str = ''
        ts_label = QLabel(ts_str)
        ts_label.setStyleSheet("font-size: 9px; color: #666;")
        bottom_row.addWidget(ts_label)

        bottom_row.addStretch()

        if is_kept:
            check_label = QLabel("✓ 已保留")
            check_label.setStyleSheet("color: #22c55e; font-weight: bold; font-size: 10px;")
            bottom_row.addWidget(check_label)

        layout.addLayout(bottom_row)

        # 点击事件切换保留状态
        frame.mousePressEvent = lambda e, sid=screenshot.screenshot_id: self._toggle_screenshot_kept(sid)

        return frame

    def _toggle_screenshot_kept(self, screenshot_id: str):
        """切换截图的保留/取消保留状态并刷新显示。

        查找截图当前状态，调用 SessionManager.set_screenshot_kept，
        然后重建步骤缩略图列表。
        """
        if not self._session:
            return

        # 查找当前状态
        current_kept = False
        for step in self._session.get_all_steps():
            for ss in step.screenshots:
                if ss.screenshot_id == screenshot_id:
                    current_kept = ss.status == 'kept'
                    break
            if current_kept:
                break

        # 切换
        self.session_manager.set_screenshot_kept(screenshot_id, not current_kept)

        # 刷新缩略图显示
        self._populate_step_frames()

    # ---- 会话总结自动保存（Task 3） ----

    def _save_summary(self):
        """从审核面板组件收集总结数据并保存到 SessionManager。

        收集概况总结、总结改进、实施人/测试人/审核人字段，
        构造 SessionSummary 对象后调用 session_manager.update_summary()。
        通过 textChanged 信号连接实现实时自动保存。
        """
        if not self._review_mode or not self._session:
            return

        overview = self._review_widgets.get('overview_edit', QTextEdit()).toPlainText().strip()
        improvements = self._review_widgets.get('improvements_edit', QTextEdit()).toPlainText().strip()
        implementer = self._review_widgets.get('implementer_edit', QLineEdit()).text().strip()
        tester = self._review_widgets.get('tester_edit', QLineEdit()).text().strip()
        reviewer = self._review_widgets.get('reviewer_edit', QLineEdit()).text().strip()

        summary = SessionSummary(
            overview=overview if overview else None,
            improvements=improvements if improvements else None,
            actual_implementer=implementer if implementer else None,
            actual_tester=tester if tester else None,
            actual_reviewer=reviewer if reviewer else None,
        )
        self.session_manager.update_summary(summary)

    # ---- Windows native event handling ----

    def nativeEvent(self, eventType, message):
        """Handle Windows native events (WM_HOTKEY)."""
        if sys.platform != 'win32':
            return False, None

        if eventType == b'windows_generic_MSG':
            msg = _WinMsg.from_address(int(message))
            if msg.message == WM_HOTKEY:
                hotkey_id = msg.wParam
                if hotkey_id == 1:
                    self._toolbar_capture()
                elif hotkey_id == 2:
                    self._toolbar_capture_only()
                elif hotkey_id == 3:
                    self._toolbar_prev()
                elif hotkey_id == 4:
                    self._toolbar_toggle_pause()
                elif hotkey_id == 5:
                    self._toolbar_stop()
                return True, None

        return False, None

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
