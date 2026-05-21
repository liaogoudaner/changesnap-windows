"""文件操作工具函数。

与 macOS 版本保持一致：
- 工作目录优先使用 CHANGESNAP_HOME 环境变量
- 录屏和报告保存到 outputs/<planName>/ 目录
- 截图表单到 screenshots/<sessionId>/ 目录
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional


def get_app_root() -> Path:
    """获取应用根目录（支持 PyInstaller 打包和源码运行）。"""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent.parent.resolve()


def get_work_dir() -> Path:
    """获取工作目录。

    优先使用 CHANGESNAP_HOME 环境变量（跨平台兼容，与 macOS 版本保持一致），
    若未设置则使用默认路径 ~/changesnap。
    """
    home = os.environ.get('CHANGESNAP_HOME')
    if home:
        return Path(home)
    return Path(os.path.expanduser("~/changesnap"))


def get_data_dir(subdir: str = "") -> Path:
    """获取数据子目录。"""
    base = get_work_dir()
    if subdir:
        return base / subdir
    return base


def ensure_dir(path: Path) -> Path:
    """确保目录存在，不存在则创建。"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(name: str) -> str:
    """将字符串转换为安全的文件名。"""
    invalid_chars = '<>:"/\\|?*'
    for c in invalid_chars:
        name = name.replace(c, '_')
    return name.strip().rstrip('.')


def copy_file(src: Path, dst: Path) -> Path:
    """复制文件到目标路径，返回目标路径。"""
    ensure_dir(dst.parent)
    shutil.copy2(str(src), str(dst))
    return dst


def get_file_size(path: Path) -> int:
    """获取文件大小（字节）。"""
    try:
        return path.stat().st_size
    except (OSError, FileNotFoundError):
        return 0


def get_free_space(path: Path) -> int:
    """获取指定路径的可用空间（字节）。"""
    try:
        stat = os.statvfs(str(path))
        return stat.f_frsize * stat.f_bavail
    except (AttributeError, OSError):
        # Windows 不支持 statvfs
        try:
            import ctypes
            free_bytes = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(str(path.anchor)),
                None, None, ctypes.pointer(free_bytes)
            )
            return free_bytes.value
        except (AttributeError, OSError):
            return 2 * 1024 ** 3  # 返回 2GB 默认值


def get_session_dir() -> Path:
    """获取会话状态保存目录。"""
    return ensure_dir(get_work_dir() / '.session')


def get_screenshots_dir(session_id: str) -> Path:
    """获取截图保存目录。"""
    return ensure_dir(get_work_dir() / 'screenshots' / session_id)


def get_screenshots_base_dir() -> Path:
    """获取截图根目录（用于路径沙箱校验）。"""
    return ensure_dir(get_work_dir() / 'screenshots')


def get_step_screenshots_dir(session_id: str, step_id: str) -> Path:
    """获取某个步骤的截图目录。"""
    return ensure_dir(get_screenshots_dir(session_id) / step_id)


def get_recordings_dir(session_id: str) -> Path:
    """获取录屏保存目录。

    新版录屏已迁移到 outputs/<planName>/ 目录（见 get_plan_output_dir），
    此函数保留向后兼容。
    """
    return ensure_dir(get_work_dir() / 'recordings' / session_id)


def get_reports_dir() -> Path:
    """获取报告和录屏输出根目录。

    位于程序所在磁盘下的 reports/ 目录：
    - PyInstaller 打包后：exe 同目录下的 reports/
    - 源码运行：当前工作目录下的 reports/
    """
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent
    else:
        base = Path.cwd()
    return ensure_dir(base / 'reports')


def get_outputs_dir() -> Path:
    """获取报告输出目录（兼容旧代码）。"""
    return get_reports_dir()


def get_plan_output_dir(plan_name: str) -> Path:
    """获取指定方案的工作输出目录。

    路径格式：reports/<planName>/

    Args:
        plan_name: 方案名称（将被自动 sanitize）
    """
    return ensure_dir(get_reports_dir() / safe_filename(plan_name))


def get_logs_dir() -> Path:
    """获取日志目录。"""
    return ensure_dir(get_work_dir() / 'logs')


def get_plans_dir() -> Path:
    """获取方案副本目录。"""
    return ensure_dir(get_work_dir() / 'plans')


def get_config_path() -> Path:
    """获取配置文件路径。"""
    config_dir = ensure_dir(get_work_dir() / 'config')
    return config_dir / 'hotkeys.json'
