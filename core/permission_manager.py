"""权限检测模块。

提供屏幕录制权限的检测与引导管理。
权限检查结果在进程生命周期内缓存，避免重复触发系统权限弹窗。
macOS 需要 TCC 屏幕录制权限；Windows 不需要系统权限。
"""

from __future__ import annotations

import sys
import subprocess
import threading

from utils.log_utils import get_logger

logger = get_logger(__name__)


class ScreenRecordingPermission:
    """屏幕录制权限检测与管理。

    macOS: 检测 TCC 屏幕录制权限。
    Windows: 无需系统权限，直接返回 True。
    """

    _cache: bool | None = None
    _lock = threading.Lock()

    @classmethod
    def check(cls) -> bool:
        """检测屏幕录制权限是否已授予。

        macOS: 通过 mss 进行一次 1x1 像素截图测试。
              如果 mss 不可用或截图失败，视为无权限。
              结果在进程生命周期内缓存，避免重复触发系统权限弹窗。
        Windows: 屏幕录制不需要系统权限，直接返回 True。

        Returns:
            True 表示有权限（或 Windows 平台），False 表示无权限
        """
        # Windows: screen recording does NOT require system permissions
        if sys.platform == 'win32':
            cls._cache = True
            return True

        with cls._lock:
            if cls._cache is not None:
                return cls._cache

        try:
            import mss

            with mss.mss() as sct:
                mon = sct.monitors[0]
                # 极小截图避免大数据量，仅用于权限检测
                sct.grab({
                    'left': mon['left'],
                    'top': mon['top'],
                    'width': 1,
                    'height': 1,
                })
            cls._cache = True
            logger.info("屏幕录制权限：已授予")
            return True
        except ImportError:
            cls._cache = False
            logger.warning("mss 未安装，无法检测屏幕录制权限")
            return False
        except Exception as e:
            # macOS TCC 拒绝时 mss 抛出 ScreenShotError 等异常
            cls._cache = False
            logger.warning(f"屏幕录制权限检测未通过: {type(e).__name__}: {e}")
            return False

    @staticmethod
    def open_settings():
        """打开系统设置 → 隐私与安全性 → 屏幕录制（仅 macOS）。"""
        if sys.platform != 'darwin':
            return
        try:
            subprocess.Popen([
                'open',
                'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture',
            ])
        except Exception as e:
            logger.warning(f"打开系统设置失败: {e}")

    @classmethod
    def reset_cache(cls):
        """重置权限检测缓存（用于测试）。"""
        with cls._lock:
            cls._cache = None
