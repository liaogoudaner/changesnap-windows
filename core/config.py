"""配置管理模块。

管理快捷键配置、录屏/截图参数。
配置加载优先级：项目根 config.yaml > ~/changesnap/config/*.json > 默认值
配置保存到 ~/changesnap/config/ (JSON 格式)。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from utils.file_utils import ensure_dir
from utils.log_utils import get_logger

logger = get_logger(__name__)

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

# 默认配置
DEFAULT_HOTKEYS = {
    "screenshot_and_advance": "Ctrl+8",
    "screenshot_only": "Ctrl+9",
    "prev_step": "Ctrl+7",
    "toggle_recording": "Ctrl+0",
    "stop_session": "Ctrl+Shift+S",
}

DEFAULT_SETTINGS = {
    "auto_advance": True,
    "screenshot_delay_ms": 200,
    "screenshot_monitor": "primary",
    "screenshot_format": "png",
    "recording_fps": 5,
    "recording_segment_minutes": 30,
    "recording_auto_compress": True,
    "work_dir": str(Path.home() / "changesnap"),
    "report_template": "default",
    # 内部标记：是否已显示过屏幕录制权限引导对话框
    "_permission_screen_shown": False,
}


def _find_project_root() -> Optional[Path]:
    """向上查找项目根目录（包含 config.yaml 的目录）。"""
    # 优先检查 PyInstaller 打包路径
    import sys
    if getattr(sys, 'frozen', False):
        meipass = Path(sys._MEIPASS)
        if (meipass / "config.yaml").exists():
            return meipass

    # 当前工作目录及父目录
    cur = Path.cwd()
    for _ in range(5):
        if (cur / "config.yaml").exists():
            return cur
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return None


class ConfigManager:
    """配置管理器。"""

    def __init__(self):
        self._hotkeys: dict = dict(DEFAULT_HOTKEYS)
        self._settings: dict = dict(DEFAULT_SETTINGS)
        self._config_dir = ensure_dir(Path.home() / "changesnap" / "config")
        self._hotkeys_file = self._config_dir / "hotkeys.json"
        self._settings_file = self._config_dir / "settings.json"
        self._load()

    def _load(self):
        """加载配置（优先级：项目 config.yaml > 用户 JSON > 默认值）。"""
        # 1. 先尝试加载项目根目录的 config.yaml
        self._load_yaml()

        # 2. 再加载 JSON（覆盖 YAML 中的值）
        self._load_json()

    def _load_yaml(self):
        """从项目根 config.yaml 加载配置。"""
        project_root = _find_project_root()
        yaml_path = project_root / "config.yaml" if project_root else None

        if not yaml_path or not yaml_path.exists():
            return

        if not YAML_AVAILABLE:
            logger.debug("PyYAML 未安装，跳过 config.yaml 加载")
            return

        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                return

            if 'hotkeys' in data and isinstance(data['hotkeys'], dict):
                for k, v in data['hotkeys'].items():
                    if k in DEFAULT_HOTKEYS and isinstance(v, str):
                        self._hotkeys[k] = v
                logger.info(f"已从 config.yaml 加载快捷键: {yaml_path}")

            if 'screenshot' in data and isinstance(data['screenshot'], dict):
                s = data['screenshot']
                if 'format' in s:
                    self._settings['screenshot_format'] = s['format']
                if 'monitor' in s:
                    self._settings['screenshot_monitor'] = s['monitor']
                if 'auto_advance' in s:
                    self._settings['auto_advance'] = bool(s['auto_advance'])

            if 'recording' in data and isinstance(data['recording'], dict):
                r = data['recording']
                if 'fps' in r:
                    self._settings['recording_fps'] = int(r['fps'])
                if 'segment_minutes' in r:
                    self._settings['recording_segment_minutes'] = int(r['segment_minutes'])
                if 'auto_compress' in r:
                    self._settings['recording_auto_compress'] = bool(r['auto_compress'])

            if 'work_dir' in data and data['work_dir']:
                self._settings['work_dir'] = str(data['work_dir'])

        except Exception as e:
            logger.warning(f"config.yaml 加载失败: {e}")

    def _load_json(self):
        """从 JSON 文件加载配置（覆盖 YAML 的值）。"""
        # 加载快捷键
        if self._hotkeys_file.exists():
            try:
                with open(self._hotkeys_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._hotkeys.update(data)
                logger.info(f"快捷键配置已加载: {self._hotkeys_file}")
            except Exception as e:
                logger.warning(f"快捷键配置加载失败，使用默认值: {e}")

        # 加载设置
        if self._settings_file.exists():
            try:
                with open(self._settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._settings.update(data)
                logger.info(f"应用设置已加载: {self._settings_file}")
            except Exception as e:
                logger.warning(f"应用设置加载失败，使用默认值: {e}")

    def save(self):
        """保存所有配置。"""
        try:
            with open(self._hotkeys_file, 'w', encoding='utf-8') as f:
                json.dump(self._hotkeys, f, ensure_ascii=False, indent=2)
            with open(self._settings_file, 'w', encoding='utf-8') as f:
                json.dump(self._settings, f, ensure_ascii=False, indent=2)
            logger.info("配置已保存")
        except Exception as e:
            logger.error(f"配置保存失败: {e}")

    # ---- 快捷键 ----

    def get_hotkey(self, action: str) -> str:
        """获取指定操作的快捷键。"""
        return self._hotkeys.get(action, DEFAULT_HOTKEYS.get(action, ""))

    def set_hotkey(self, action: str, key: str):
        """设置指定操作的快捷键。"""
        self._hotkeys[action] = key

    def get_all_hotkeys(self) -> dict:
        """获取所有快捷键配置。"""
        return dict(self._hotkeys)

    def set_all_hotkeys(self, hotkeys: dict):
        """批量设置快捷键。"""
        self._hotkeys.update(hotkeys)

    # ---- 设置 ----

    def get_setting(self, key: str, default=None):
        """获取设置项。"""
        return self._settings.get(key, default)

    def set_setting(self, key: str, value):
        """设置设置项。"""
        self._settings[key] = value

    def get_all_settings(self) -> dict:
        """获取所有设置。"""
        return dict(self._settings)

    def set_all_settings(self, settings: dict):
        """批量设置。"""
        self._settings.update(settings)

    # ---- 默认配置导出 ----

    @staticmethod
    def create_default_config_yaml(path: Optional[Path] = None):
        """创建默认 config.yaml。"""
        if path is None:
            path = Path.cwd() / 'config.yaml'
        content = f"""# ChangeSnap 默认配置
# 快捷键配置
hotkeys:
  screenshot_and_advance: "{DEFAULT_HOTKEYS['screenshot_and_advance']}"
  screenshot_only: "{DEFAULT_HOTKEYS['screenshot_only']}"
  prev_step: "{DEFAULT_HOTKEYS['prev_step']}"
  toggle_recording: "{DEFAULT_HOTKEYS['toggle_recording']}"
  stop_session: "{DEFAULT_HOTKEYS['stop_session']}"

# 截图设置
screenshot:
  format: "{DEFAULT_SETTINGS['screenshot_format']}"
  monitor: "{DEFAULT_SETTINGS['screenshot_monitor']}"
  auto_advance: {str(DEFAULT_SETTINGS['auto_advance']).lower()}

# 录屏设置
recording:
  fps: {DEFAULT_SETTINGS['recording_fps']}
  segment_minutes: {DEFAULT_SETTINGS['recording_segment_minutes']}
  auto_compress: {str(DEFAULT_SETTINGS['recording_auto_compress']).lower()}

# 工作目录
work_dir: "{DEFAULT_SETTINGS['work_dir']}"
"""
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"默认配置已创建: {path}")
