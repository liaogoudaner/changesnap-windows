"""截图引擎模块。

使用 mss 进行跨平台高性能截图，支持多显示器选择。
截图保存为 PNG 格式，并提供截图的辅助功能。

Windows 兼容性说明:
- mss 在 Windows 上原生工作，无需额外配置。
- 高 DPI 屏幕可能需要应用程序级别设置 DPI 感知
  (SetProcessDPIAware / SetProcessDpiAwareness) 以确保截图区域
  坐标与系统缩放比例一致。参见 app.py 中的启动初始化。
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from models.screenshot import ScreenshotMeta
from utils.file_utils import ensure_dir, get_step_screenshots_dir, get_screenshots_dir
from utils.log_utils import get_logger

logger = get_logger(__name__)


class ScreenshotEngine:
    """截图引擎。"""

    def __init__(self):
        self._mss_available = False
        self._mss = None
        self._init_mss()

    def _init_mss(self):
        """初始化 mss。"""
        try:
            import mss
            self._mss = mss.mss()
            self._mss_available = True
            logger.info("mss 截图引擎初始化成功")
        except ImportError:
            logger.warning("mss 未安装，截图引擎降级模式")
            self._mss_available = False
        except Exception as e:
            logger.warning(f"mss 初始化失败: {e}")
            self._mss_available = False

    @property
    def is_available(self) -> bool:
        return self._mss_available

    def get_monitors(self) -> list[dict]:
        """获取所有显示器信息。

        Returns:
            显示器列表，每项包含 id, width, height, left, top
        """
        if not self._mss_available:
            return [{'id': 0, 'name': '主显示器', 'width': 1920, 'height': 1080, 'left': 0, 'top': 0}]

        try:
            monitors = []
            for i, m in enumerate(self._mss.monitors):
                if i == 0:
                    continue  # 跳过组合显示器
                monitors.append({
                    'id': i,
                    'name': f"显示器 {i}",
                    'width': m.get('width', 0),
                    'height': m.get('height', 0),
                    'left': m.get('left', 0),
                    'top': m.get('top', 0),
                })
            if not monitors:
                # 至少返回一个
                m = self._mss.monitors[0]
                monitors.append({
                    'id': 0,
                    'name': '主显示器',
                    'width': m.get('width', 1920),
                    'height': m.get('height', 1080),
                    'left': m.get('left', 0),
                    'top': m.get('top', 0),
                })
            return monitors
        except Exception as e:
            logger.error(f"获取显示器信息失败: {e}")
            return [{'id': 0, 'name': '主显示器', 'width': 1920, 'height': 1080, 'left': 0, 'top': 0}]

    def capture(
        self,
        session_id: str,
        step_id: str,
        seq: int = 0,
        monitor: str = 'primary',
        region: Optional[dict] = None,
        screenshot_dir: Optional[Path] = None,
    ) -> Optional[ScreenshotMeta]:
        """执行截图。

        Args:
            session_id: 会话 ID
            region: 自定义截图区域 {'left': int, 'top': int, 'width': int, 'height': int}，优先于 monitor
            step_id: 步骤 ID 或 'orphan'
            seq: 步骤序号
            monitor: 'primary' / 'all' / 显示器索引
            screenshot_dir: 截图保存目录，默认自动生成

        Returns:
            截图元数据，失败返回 None
        """
        if not self._mss_available:
            logger.warning("截图引擎未初始化，尝试重新初始化")
            self._init_mss()
            if not self._mss_available:
                logger.error("截图引擎不可用，无法截图")
                return None

        try:
            start_time = time.time()

            # 获取当前时间戳
            now = datetime.now(timezone(timedelta(hours=8)))
            timestamp_str = now.strftime('%H-%M-%S')
            timestamp_iso = now.isoformat()

            # 确定截图目录
            if screenshot_dir:
                dir_path = screenshot_dir
            else:
                dir_path = get_step_screenshots_dir(session_id, step_id)

            ensure_dir(dir_path)

            # 选择显示器并截图
            monitors = self.get_monitors()
            selected_monitors = []

            if monitor == 'primary' or monitor == 'all':
                selected_monitors = [m for m in monitors if m['id'] > 0] or monitors[:1]
                if monitor == 'primary':
                    selected_monitors = selected_monitors[:1]
            else:
                try:
                    idx = int(monitor)
                    m = next((m for m in monitors if m['id'] == idx), None)
                    if m:
                        selected_monitors = [m]
                    else:
                        selected_monitors = monitors[:1]
                except (ValueError, TypeError):
                    selected_monitors = monitors[:1]

            saved_files = []
            for m in selected_monitors:
                # 生成文件名
                ss_id = str(uuid.uuid4())[:8]
                filename = f"{seq:03d}_{timestamp_str}_screenshot.png"

                # 如果 step_id 是 orphan
                if step_id == 'orphan':
                    filename = f"unassigned_{timestamp_str}_screenshot.png"

                filepath = dir_path / filename

                # 截图区域：优先使用自定义 region
                if region:
                    monitor_dict = {
                        'left': region['left'],
                        'top': region['top'],
                        'width': region['width'],
                        'height': region['height'],
                    }
                else:
                    monitor_dict = {
                        'left': m['left'],
                        'top': m['top'],
                        'width': m['width'],
                        'height': m['height'],
                    }

                sct = self._mss  # type: ignore
                sct_img = sct.grab(monitor_dict)

                # 保存 PNG
                from PIL import Image
                img = Image.frombytes('RGB', sct_img.size, sct_img.rgb)
                img.save(str(filepath), 'PNG')

                # 计算 MD5
                md5_hash = hashlib.md5()
                with open(filepath, 'rb') as f:
                    md5_hash.update(f.read())

                file_size = filepath.stat().st_size

                meta = ScreenshotMeta(
                    screenshot_id=ss_id,
                    filename=filename,
                    timestamp=timestamp_iso,
                    step_id=step_id,
                    seq=seq,
                    width=m['width'],
                    height=m['height'],
                    file_size_bytes=file_size,
                    md5=md5_hash.hexdigest(),
                    monitor_index=m['id'],
                    status='active',
                    taken_while_paused=False,
                    filepath=str(filepath),
                )

                elapsed = (time.time() - start_time) * 1000
                logger.info(
                    f"截图完成: {filename} "
                    f"({m['width']}x{m['height']}, {file_size // 1024}KB, {elapsed:.0f}ms)"
                )

                saved_files.append(meta)

            # 如果是 'primary' 或指定单个显示器，返回单张
            if saved_files:
                return saved_files[0] if monitor != 'all' else saved_files[0]

            return None

        except Exception as e:
            logger.error(f"截图失败: {e}", exc_info=True)
            return None

    def capture_fallback(self) -> Optional[Path]:
        """备选截图方案（使用 PIL.ImageGrab）。

        Windows 兼容性: PIL.ImageGrab.grab() 在 Windows 上原生工作，
        无需额外依赖。
        """
        try:
            from PIL import ImageGrab
            now = datetime.now(timezone(timedelta(hours=8)))
            timestamp_str = now.strftime('%H-%M-%S')
            filename = f"fallback_{timestamp_str}.png"
            filepath = Path.home() / 'changesnap' / 'screenshots' / 'fallback' / filename
            ensure_dir(filepath.parent)

            img = ImageGrab.grab()
            img.save(str(filepath), 'PNG')
            logger.info(f"备选截图完成: {filename}")
            return filepath
        except Exception as e:
            logger.error(f"备选截图失败: {e}")
            return None
