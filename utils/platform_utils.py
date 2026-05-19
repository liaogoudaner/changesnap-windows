"""跨平台适配工具函数。"""

from __future__ import annotations

import platform
import subprocess
import sys


def get_platform() -> str:
    """获取当前平台名称。"""
    return sys.platform


def is_macos() -> bool:
    """是否 macOS。"""
    return sys.platform == 'darwin'


def is_windows() -> bool:
    """是否 Windows。"""
    return sys.platform == 'win32'


def is_linux() -> bool:
    """是否 Linux。"""
    return sys.platform == 'linux'


def get_platform_display() -> str:
    """获取可读的平台名称。"""
    return f"{platform.system()} {platform.release()} ({platform.machine()})"


def open_browser(url: str) -> bool:
    """在默认浏览器中打开 URL。"""
    try:
        if is_macos():
            subprocess.Popen(['open', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif is_windows():
            subprocess.Popen(['cmd', '/c', 'start', url], shell=True)
        elif is_linux():
            subprocess.Popen(['xdg-open', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            import webbrowser
            webbrowser.open(url)
        return True
    except Exception:
        try:
            import webbrowser
            webbrowser.open(url)
            return True
        except Exception:
            return False


def get_ffmpeg_cmd() -> str:
    """获取 ffmpeg 命令名。"""
    return 'ffmpeg'


def check_ffmpeg() -> bool:
    """检查 ffmpeg 是否可用。"""
    try:
        subprocess.run(
            [get_ffmpeg_cmd(), '-version'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return False


def get_screen_recording_cmd(
    output_path: str,
    fps: int = 5,
    monitor: int = 1,
    display: str = '',
) -> list[str]:
    """构造 ffmpeg 录屏命令。

    Args:
        output_path: 输出文件路径
        fps: 帧率
        monitor: 显示器索引
        display: macOS 上的显示名称

    Returns:
        ffmpeg 命令参数列表
    """
    cmd = [get_ffmpeg_cmd(), '-y']

    if is_macos():
        # macOS 使用 AVFoundation
        input_dev = display or f"{monitor - 1}"
        cmd.extend([
            '-f', 'avfoundation',
            '-capture_cursor', '1',
            '-capture_mouse_clicks', '1',
            '-i', f'"{input_dev}"',
        ])
    elif is_windows():
        # Windows 使用 gdigrab
        cmd.extend([
            '-f', 'gdigrab',
            '-i', 'desktop',
        ])
    elif is_linux():
        # Linux 使用 x11grab
        cmd.extend([
            '-f', 'x11grab',
            '-i', f':0.0',
        ])

    cmd.extend([
        '-r', str(fps),
        '-vcodec', 'libx264',
        '-preset', 'ultrafast',
        '-pix_fmt', 'yuv420p',
        '-crf', '28',
        output_path,
    ])

    return cmd
