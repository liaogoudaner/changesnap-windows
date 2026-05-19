"""全局快捷键管理模块。

使用 pynput.keyboard.Listener 手动检测组合键（兼容 Python 3.14 / macOS 15 / Windows）。
Listener 需要 macOS 辅助功能权限；Windows 上使用 pynput Listener（Win32 RegisterHotKey
作为未来增强保留）。
"""

from __future__ import annotations

import sys
import threading
from typing import Callable, Optional

from utils.log_utils import get_logger

logger = get_logger(__name__)

# 修饰键标准化映射
_MODIFIER_MAP = {
    'ctrl':  '<ctrl>',
    'shift': '<shift>',
    'alt':   '<alt>',
    'cmd':   '<cmd>',
}


class HotkeyManager:
    """全局快捷键管理器。使用 Listener 替代 GlobalHotKeys。"""

    def __init__(self, config_manager=None):
        self._listener = None
        self._running = False
        self._actions: dict[str, Callable] = {}
        self._config = config_manager
        self._pynput_available = False
        self._active_modifiers = set()
        self._hotkey_combos: dict[str, Callable] = {}  # normalized_combo -> callback
        self._check_pynput()

    def _check_pynput(self):
        try:
            import pynput
            self._pynput_available = True
        except ImportError:
            self._pynput_available = False

    @property
    def is_available(self) -> bool:
        return self._pynput_available

    @property
    def is_listening(self) -> bool:
        return self._running

    # ---- 启动/停止 ----

    def start(self, hotkey_map: dict[str, tuple[str, Callable]]):
        """启动快捷键监听。"""
        if not self._pynput_available:
            return
        if self._running:
            return

        # 构建标准化组合键映射
        for action, (key_combo, callback) in hotkey_map.items():
            self._actions[action] = callback
            normalized = self._normalize_combo(key_combo)
            self._hotkey_combos[normalized] = callback
            logger.info(f"注册: {action} -> {key_combo} -> {normalized}")

        self._running = True
        self._listener_thread = threading.Thread(target=self._listen, daemon=True)
        self._listener_thread.start()
        logger.info(f"快捷键监听已启动 ({len(self._hotkey_combos)} 个)")

        # 在 Windows 上附加尝试 Win32 RegisterHotKey（增强支持）
        self._start_win32_hotkeys()

    def _listen(self):
        """后台监听线程。使用 Listener（兼容 Python 3.14 / macOS 15 / Windows）。"""
        try:
            from pynput import keyboard

            def on_press(key):
                try:
                    # 追踪修饰键
                    if hasattr(key, 'name'):
                        if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                            self._active_modifiers.add('ctrl')
                        elif key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
                            self._active_modifiers.add('shift')
                        elif key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r):
                            self._active_modifiers.add('alt')
                        elif key in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r):
                            self._active_modifiers.add('cmd')
                        return  # 单独的修饰键不触发

                    # 获取按键字符
                    key_char = self._key_to_char(key)
                    if not key_char:
                        return

                    # 构建当前组合键
                    parts = sorted(self._active_modifiers)
                    parts.append(key_char)
                    combo = '+'.join(parts)

                    # 匹配注册的快捷键
                    callback = self._hotkey_combos.get(combo)
                    if callback:
                        logger.debug(f"快捷键触发: {combo}")
                        try:
                            callback()
                        except Exception as e:
                            logger.error(f"快捷键回调异常: {e}")
                except Exception:
                    pass  # 单个按键处理异常不影响监听器

            def on_release(key):
                try:
                    if hasattr(key, 'name'):
                        if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                            self._active_modifiers.discard('ctrl')
                        elif key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
                            self._active_modifiers.discard('shift')
                        elif key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r):
                            self._active_modifiers.discard('alt')
                        elif key in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r):
                            self._active_modifiers.discard('cmd')
                except Exception:
                    pass

            self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._listener.start()
            logger.info("Listener 已启动，等待按键...")
            self._listener.join()  # 阻塞直到 stop()

        except ImportError:
            self._pynput_available = False
            logger.warning("pynput 未安装")
        except Exception as e:
            logger.warning(f"Listener 异常退出: {e}")
        finally:
            self._running = False
            self._active_modifiers.clear()
            logger.info("Listener 已停止")

    def _start_win32_hotkeys(self):
        """Windows-only: Register system-level hotkeys via Win32 RegisterHotKey.

        This provides a more robust alternative to pynput on Windows.
        Currently structured as a future enhancement note — pynput Listener
        is the primary mechanism on Windows.
        """
        if sys.platform != 'win32':
            return

        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        # MOD_CONTROL = 0x0002, MOD_SHIFT = 0x0004, MOD_NOREPEAT = 0x4000
        MOD_CONTROL = 0x0002
        MOD_SHIFT = 0x0004
        MOD_NOREPEAT = 0x4000

        # Virtual key codes for number keys
        VK_MAP = {
            '8': 0x38,  # '8' key
            '9': 0x39,  # '9' key
            '7': 0x37,  # '7' key
            '0': 0x30,  # '0' key
            'S': 0x53,  # 'S' key
        }

        # Map actions to our callbacks
        for action, (combo, callback) in self._actions.items():
            # Note: RegisterHotKey requires a message loop to receive WM_HOTKEY.
            # PySide6's QApplication already provides a message loop, but we need
            # to hook into it. For simplicity, pynput Listener is the primary
            # mechanism on Windows; RegisterHotKey is noted as a future enhancement.
            pass

        logger.info("Windows hotkeys: using pynput Listener (RegisterHotKey enhancement pending)")

    def _key_to_char(self, key) -> Optional[str]:
        """将 pynput Key 转为字符串。"""
        try:
            # 普通字符键：key.char
            if hasattr(key, 'char') and key.char:
                return key.char.lower()
            # 特殊键：key.name
            if hasattr(key, 'name') and key.name:
                return key.name.lower()
        except Exception:
            pass
        return None

    def _normalize_combo(self, key_combo: str) -> str:
        """将 'Ctrl+8' 标准化为 'ctrl+8'（与 Listener 内部表示一致）。"""
        parts = key_combo.strip().lower().split('+')
        normalized = []
        for p in parts:
            p = p.strip()
            if p in ('ctrl', 'control', 'ctl'):
                normalized.append('ctrl')
            elif p == 'shift':
                normalized.append('shift')
            elif p == 'alt':
                normalized.append('alt')
            elif p in ('cmd', 'win', 'super', 'command'):
                normalized.append('cmd')
            else:
                normalized.append(p)  # 保持字符原样（已 lower）
        return '+'.join(sorted(normalized))

    def unregister_all(self):
        """停止监听。"""
        self._running = False
        self._hotkey_combos.clear()
        self._actions.clear()
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        logger.info("所有快捷键已注销")

    def check_hotkey_conflict(self, key_combo: str) -> Optional[str]:
        for action, cb in self._actions.items():
            config = self._config
            existing = config.get_hotkey(action)
            if existing == key_combo:
                return action
        return None
