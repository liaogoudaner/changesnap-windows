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
        self._win32_hotkey_ids: list[int] = []
        self._win32_hwnd: Optional[int] = None
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
            self._actions[action] = (key_combo, callback)
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

        RegisterHotKey is the most reliable global hotkey mechanism on Windows.
        It registers hotkeys at the OS level and the system sends WM_HOTKEY
        messages to the specified window regardless of which app is active.
        """
        if sys.platform != 'win32':
            return

        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        MOD_CONTROL = 0x0002
        MOD_SHIFT = 0x0004
        MOD_NOREPEAT = 0x4000
        MOD_ALT = 0x0001

        VK_MAP = {
            '8': 0x38,
            '9': 0x39,
            '7': 0x37,
            '0': 0x30,
        }

        registered_count = 0

        for action, (combo, callback) in self._actions.items():
            # Determine modifiers and virtual key
            parts = [p.strip().lower() for p in combo.split('+')]
            modifiers = 0
            vk = 0

            for p in parts:
                if p in ('ctrl', 'control'):
                    modifiers |= MOD_CONTROL
                elif p == 'shift':
                    modifiers |= MOD_SHIFT
                elif p in ('alt',):
                    modifiers |= MOD_ALT
                else:
                    # Single character key
                    if len(p) == 1:
                        vk = ord(p.upper())  # Virtual key code for letters/numbers
                    elif p in VK_MAP:
                        vk = VK_MAP[p]

            if vk == 0:
                logger.warning(f"Win32: Could not determine VK for '{combo}'")
                continue

            # Generate a unique hotkey ID
            # Each action gets a unique ID (1-5)
            action_ids = {
                'screenshot_and_advance': 1,
                'screenshot_only': 2,
                'prev_step': 3,
                'toggle_recording': 4,
                'stop_session': 5,
            }
            hotkey_id = action_ids.get(action, registered_count + 1)

            modifiers |= MOD_NOREPEAT  # Prevent key repeat

            # Get the main window's HWND
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if not app:
                logger.warning("Win32: QApplication not available for RegisterHotKey")
                continue

            # Find the main window
            hwnd = None
            for widget in app.topLevelWidgets():
                if hasattr(widget, 'winId') and widget.isWindow():
                    hwnd = int(widget.winId())
                    break

            if not hwnd:
                logger.warning("Win32: No window HWND available for RegisterHotKey")
                continue

            result = user32.RegisterHotKey(hwnd, hotkey_id, modifiers, vk)
            if result:
                logger.info(f"Win32 RegisterHotKey: {combo} (id={hotkey_id}) OK")
                registered_count += 1
            else:
                err = kernel32.GetLastError()
                logger.warning(f"Win32 RegisterHotKey failed: {combo} (err={err})")

        if registered_count > 0:
            logger.info(f"Win32 hotkeys registered: {registered_count}/{len(self._actions)}")
        else:
            logger.warning("Win32: No hotkeys registered, falling back to pynput only")

        # Store for cleanup
        self._win32_hotkey_ids = list(range(1, 6))
        self._win32_hwnd = hwnd if registered_count > 0 else None

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

        # Unregister Win32 hotkeys
        if sys.platform == 'win32' and self._win32_hwnd:
            import ctypes
            user32 = ctypes.windll.user32
            for hotkey_id in self._win32_hotkey_ids:
                user32.UnregisterHotKey(self._win32_hwnd, hotkey_id)
            logger.info("Win32 hotkeys unregistered")

        logger.info("所有快捷键已注销")

    def check_hotkey_conflict(self, key_combo: str) -> Optional[str]:
        for action, cb in self._actions.items():
            config = self._config
            existing = config.get_hotkey(action)
            if existing == key_combo:
                return action
        return None
