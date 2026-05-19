#!/usr/bin/env python3
"""
ChangeSnap — 变更实施记录与报告生成工具

原生桌面应用，基于 PySide6。
- 加载 Word/Excel/JSON/YAML 变更方案
- 屏幕录制 + 全局快捷键截图
- 预览确认后一键生成 Word 总结报告

启动: python app.py  或  双击打包后的 ChangeSnap.exe
"""

from __future__ import annotations

import sys
from pathlib import Path

# PyInstaller 打包后需要 _MEIPASS 路径
if getattr(sys, 'frozen', False):
    _meipass = Path(sys._MEIPASS)
    if str(_meipass) not in sys.path:
        sys.path.insert(0, str(_meipass))

# 添加项目根目录
_app_root = Path(__file__).parent.resolve()
if str(_app_root) not in sys.path:
    sys.path.insert(0, str(_app_root))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from utils.log_utils import setup_logger
from gui.main_window import MainWindow


def main():
    setup_logger('changesnap', level=20)

    # Windows DPI awareness: enable per-monitor DPI before creating QApplication
    if sys.platform == 'win32':
        import ctypes
        # Set process-level DPI awareness so that screenshot / window coordinates
        # are consistent with system scaling on high-DPI displays.
        ctypes.windll.user32.SetProcessDPIAware()

    app = QApplication(sys.argv)
    app.setApplicationName("ChangeSnap")
    app.setOrganizationName("ChangeSnap")

    app.setStyleSheet("""
        QMainWindow { background: #f5f5f5; }
        QPushButton {
            background: #007aff; color: white; border: none;
            border-radius: 6px; padding: 8px 16px; font-size: 13px;
        }
        QPushButton:hover { background: #0056cc; }
        QPushButton:disabled { background: #ccc; color: #888; }
        QPushButton#btnLoad { background: #34c759; }
        QPushButton#btnLoad:hover { background: #28a745; }
        QPushButton#btnStop { background: #ff3b30; }
        QPushButton#btnStop:hover { background: #cc0000; }
        QTreeWidget { font-size: 13px; border: 1px solid #ddd; border-radius: 4px; }
        QGroupBox { font-weight: bold; border: 1px solid #ddd; border-radius: 6px; margin-top: 1ex; padding-top: 1ex; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
        QStatusBar { background: #e8e8e8; border-top: 1px solid #ccc; }
    """)

    window = MainWindow()
    window.show()

    # --auto-test: 自动加载方案并测试开始变更
    if '--auto-test' in sys.argv:
        test_plan = None
        for a in sys.argv:
            if a.endswith('.docx') or a.endswith('.xlsx') or a.endswith('.json'):
                test_plan = a
                break
        if test_plan:
            def _auto_test():
                from pathlib import Path
                from PySide6.QtWidgets import QMessageBox
                try:
                    window._plan = window.plan_parser.parse(Path(test_plan))
                    window._populate_step_tree()
                    window._btn_start.setEnabled(True)
                    window._lbl_plan_info.setText(f"{window._plan.basic_info.product_name} | {window._plan.total_steps()} 步")
                    QTimer.singleShot(500, window._start_session)
                except Exception as e:
                    QMessageBox.critical(window, "Auto-test Error", str(e))
            QTimer.singleShot(2000, _auto_test)

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
