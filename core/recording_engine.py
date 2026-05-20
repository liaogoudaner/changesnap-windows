"""录屏引擎模块。

使用 ffmpeg 子进程进行跨平台屏幕录制（替代 mss 方案）。
macOS 通过 AVFoundation 捕获，Windows 通过 gdigrab 捕获。
ffmpeg 二进制由 imageio-ffmpeg 提供（同时搜索系统路径），无需外部安装。
所有操作异常安全，不会导致进程崩溃。

输出路径与 macOS 版本保持一致：
    outputs/<planName>/recording_XXX.mp4
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from utils.file_utils import ensure_dir, get_work_dir, safe_filename
from utils.log_utils import get_logger

logger = get_logger(__name__)


class RecordingEngine:
    """录屏引擎。所有方法异常安全。"""

    def __init__(self):
        self._running = False
        self._paused = False
        self._session_id = ""
        self._plan_name = ""
        self._fps = 5
        self._segment_minutes = 30
        self._current_segment = 0
        self._start_time: Optional[float] = None
        self._output_path = ""
        self._ffmpeg_path: Optional[str] = None
        self._macos_device: Optional[str] = None
        self._ffmpeg_process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._segment_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._checked_available: Optional[bool] = None
        self._region: Optional[dict] = None

    # ---- 公开属性 ----

    @property
    def is_available(self) -> bool:
        """检查 ffmpeg 是否可用。"""
        if self._checked_available is None:
            self._checked_available = self._probe_available()
        return self._checked_available

    @property
    def is_recording(self) -> bool:
        """是否正在录制（未暂停）。"""
        return self._running and not self._paused

    @property
    def is_paused(self) -> bool:
        """是否已暂停。"""
        return self._running and self._paused

    @property
    def elapsed_seconds(self) -> float:
        """从开始录制到现在的总秒数（含暂停时间）。"""
        if self._start_time is not None:
            return time.time() - self._start_time
        return 0.0

    # ---- 公开方法 ----

    def start(
        self,
        session_id: str,
        fps: int = 5,
        segment_minutes: int = 30,
        region: Optional[dict] = None,
        plan_name: str = "",
    ) -> bool:
        """开始录制。

        Args:
            session_id: 会话 ID
            fps: 帧率 (1-30)
            segment_minutes: 分段切换间隔（分钟）
            region: 区域选择 dict，包含 left, top, width, height（屏幕坐标）
            plan_name: 方案名称，用于构造 outputs/<planName>/ 输出路径

        Returns:
            是否成功启动
        """
        if not self.is_available:
            return False
        with self._lock:
            if self._running:
                return False
            try:
                self._session_id = session_id
                self._plan_name = plan_name or session_id
                self._region = region
                self._fps = max(1, min(fps, 30))
                self._segment_minutes = max(1, segment_minutes)
                self._current_segment = 1
                self._running = True
                self._paused = False
                self._start_time = time.time()

                self._output_path = self._build_output_path(self._current_segment)

                if not self._start_ffmpeg(self._output_path):
                    self._running = False
                    return False

                self._start_monitor()
                self._start_segment_timer()
                logger.info(
                    f"录屏已开始 (session={session_id}, plan={self._plan_name}, "
                    f"fps={self._fps}, segment={self._segment_minutes}min, "
                    f"region={region})"
                )
                return True
            except Exception as e:
                logger.error(f"录屏启动失败: {e}")
                self._running = False
                self._ffmpeg_process = None
                return False

    def stop(self) -> bool:
        """停止录制。

        先发送 SIGINT 让 ffmpeg 优雅退出以写入完整的 MP4 文件，
        超时后强制终止。
        """
        with self._lock:
            if not self._running:
                return False
            try:
                elapsed = time.time() - (self._start_time or time.time())
                self._running = False
                self._paused = False
                self._stop_segment_timer()
                self._stop_ffmpeg(sigint_first=True)
                time.sleep(0.3)

                if self._monitor_thread and self._monitor_thread.is_alive():
                    self._monitor_thread.join(timeout=3)
                    self._monitor_thread = None

                self._start_time = None
                logger.info(f"录屏已停止 (时长: {elapsed:.0f}s)")
                return True
            except Exception as e:
                logger.error(f"停止录屏异常: {e}")
                return False

    def pause(self) -> bool:
        """暂停录制。停止 ffmpeg 进程但不启动新进程。"""
        with self._lock:
            if not self._running or self._paused:
                return False
            try:
                self._paused = True
                self._stop_segment_timer()
                self._stop_ffmpeg(sigint_first=True)
                logger.info("录屏已暂停")
                return True
            except Exception as e:
                logger.error(f"暂停录屏异常: {e}")
                return False

    def resume(self) -> bool:
        """恢复录制。启动新的 ffmpeg 进程写入新分段文件。"""
        with self._lock:
            if not self._running or not self._paused:
                return False
            try:
                self._paused = False
                self._current_segment += 1
                self._output_path = self._build_output_path(self._current_segment)

                if not self._start_ffmpeg(self._output_path):
                    self._running = False
                    return False

                self._start_segment_timer()
                logger.info(f"录屏已恢复 (分段 {self._current_segment})")
                return True
            except Exception as e:
                logger.error(f"恢复录屏异常: {e}")
                return False

    def get_status(self) -> dict:
        """获取引擎当前状态。"""
        with self._lock:
            return {
                "is_recording": self._running and not self._paused,
                "is_paused": self._paused,
                "is_available": self.is_available,
                "elapsed_seconds": self.elapsed_seconds,
                "current_segment": self._current_segment if self._running else 0,
                "session_id": self._session_id,
            }

    # ---- 可用性探测 ----

    def _probe_available(self) -> bool:
        """探测 ffmpeg 是否可用，并缓存路径。"""
        try:
            path = self._find_ffmpeg()
            if not path:
                logger.warning("录屏引擎不可用：未找到 ffmpeg")
                return False
            self._ffmpeg_path = path

            if sys.platform == "darwin":
                device = self._detect_macos_screen_device(path)
                if device:
                    logger.info(f"macOS 录屏设备: {device}")
                    self._macos_device = device
                else:
                    self._macos_device = "1:0"  # 默认尝试
                    logger.info("未检测到录屏设备，使用默认设备 1:0")

            logger.info(f"录屏引擎可用 (ffmpeg={path})")
            return True
        except Exception as e:
            logger.warning(f"录屏引擎不可用: {e}")
            return False

    def _find_ffmpeg(self) -> Optional[str]:
        """在多个位置搜索 ffmpeg 可执行文件。

        搜索顺序:
        1. imageio-ffmpeg 捆绑的二进制
        2. 系统 PATH
        3. 常见安装路径（含 Windows 特有路径）
        """
        # 1. imageio-ffmpeg 捆绑的二进制
        try:
            from imageio_ffmpeg import get_ffmpeg_exe

            path = get_ffmpeg_exe()
            if path and os.path.isfile(path):
                return path
        except Exception:
            pass

        # 1.5 PyInstaller _MEIPASS — 搜索 imageio_ffmpeg 的捆绑二进制
        if getattr(sys, 'frozen', False):
            meipass = sys._MEIPASS
            _search_dirs = [
                os.path.join(meipass, 'imageio_ffmpeg', 'binaries'),
                os.path.join(meipass, 'binaries'),
                meipass,
            ]
            for _d in _search_dirs:
                if not os.path.isdir(_d):
                    continue
                try:
                    for _fn in os.listdir(_d):
                        if (_fn.startswith('ffmpeg-') or _fn == 'ffmpeg.exe') and _fn.endswith('.exe'):
                            _fp = os.path.join(_d, _fn)
                            if os.path.isfile(_fp):
                                logger.info(f"ffmpeg 在 _MEIPASS 中找到: {_fp}")
                                return _fp
                except Exception:
                    continue

        # 2. 系统 PATH
        path = shutil.which("ffmpeg")
        if path:
            return path

        # 3. 常见安装路径
        common_paths = [
            "/usr/local/bin/ffmpeg",
            "/opt/homebrew/bin/ffmpeg",
            "/usr/bin/ffmpeg",
        ]

        # Windows-specific paths (in addition to imageio-ffmpeg and PATH search)
        if sys.platform == 'win32':
            # PyInstaller bundle directory
            if getattr(sys, 'frozen', False):
                bundle_dir = os.path.dirname(sys.executable)
                common_paths.append(os.path.join(bundle_dir, 'ffmpeg.exe'))
            # Common install locations
            common_paths.extend([
                os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'imageio', 'ffmpeg', 'ffmpeg.exe'),
                os.path.join(os.environ.get('ProgramFiles', 'C:\\Program Files'), 'ffmpeg', 'bin', 'ffmpeg.exe'),
                'C:\\ffmpeg\\bin\\ffmpeg.exe',
            ])

        for p in common_paths:
            if os.path.isfile(p):
                return p

        return None

    @staticmethod
    def _detect_macos_screen_device(ffmpeg_path: str) -> Optional[str]:
        """检测 macOS AVFoundation 屏幕捕获设备。

        运行 ffmpeg -f avfoundation -list_devices true -i "" 并解析输出，
        寻找屏幕捕获设备（非摄像头设备）。

        Returns:
            设备标识字符串（如 "1"），或 None（未找到）
        """
        try:
            result = subprocess.run(
                [ffmpeg_path, "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                capture_output=True,
                text=True,
                timeout=10,
            )
            output = result.stderr  # ffmpeg 设备列表输出在 stderr
        except Exception:
            return None

        # 解析设备列表
        # 输出格式示例:
        #   [AVFoundation indev @ ...] AVFoundation video devices:
        #   [AVFoundation indev @ ...] [0] FaceTime Camera
        #   [AVFoundation indev @ ...] [1] Capture screen 0
        #   [AVFoundation indev @ ...] [2] Some other camera
        # 我们需要找到屏幕设备（不是普通摄像头）
        in_video_section = False
        for line in output.splitlines():
            if "AVFoundation video devices:" in line:
                in_video_section = True
                continue
            if in_video_section:
                if "AVFoundation audio devices:" in line:
                    break
                match = re.search(r"\[(\d+)\]\s+(.+)", line)
                if match:
                    idx = match.group(1)
                    name = match.group(2).strip()
                    # 屏幕设备的特点是名称包含 screen / 显示器 / 屏幕 / display
                    # 且不是普通摄像头
                    screen_keywords = [
                        "screen", "display", "monitor", "capture",
                        "屏幕", "显示器", "录屏",
                    ]
                    camera_keywords = [
                        "camera", "facetime", "相机", "摄像头",
                    ]
                    is_screen = any(kw in name.lower() for kw in screen_keywords)
                    is_camera = any(kw in name.lower() for kw in camera_keywords)
                    if is_screen and not is_camera:
                        return idx

        # 如果找不到明确的屏幕设备，尝试一些常见索引
        # 在 macOS 上，屏幕设备通常是索引 1（索引 0 通常是摄像头）
        return None

    # ---- 路径构建 ----

    def _build_output_path(self, segment_id: int) -> str:
        """构建录屏分段输出路径。

        路径格式（与 macOS 版本保持一致）:
            outputs/<planName>/[变更录像]<planName>.mp4

        Args:
            segment_id: 分段编号（1-based）
        """
        output_dir = ensure_dir(
            get_work_dir() / 'outputs' / safe_filename(self._plan_name)
        )
        safe_name = safe_filename(self._plan_name)
        return str(output_dir / f"[变更录像]{safe_name}.mp4")

    # ---- ffmpeg 进程管理 ----

    def _get_ffmpeg_path(self) -> Optional[str]:
        """获取 ffmpeg 可执行文件路径（已缓存）。"""
        if self._ffmpeg_path is not None:
            return self._ffmpeg_path
        path = self._find_ffmpeg()
        if path:
            self._ffmpeg_path = path
        return path

    def _start_ffmpeg(self, output_path: str) -> bool:
        """启动 ffmpeg 子进程进行屏幕录制。

        Args:
            output_path: 输出文件路径

        Returns:
            进程是否成功启动
        """
        ffmpeg_path = self._get_ffmpeg_path()
        if not ffmpeg_path:
            return False

        try:
            if sys.platform == "darwin":
                cmd = self._build_macos_cmd(ffmpeg_path, output_path)
            elif sys.platform == "win32":
                cmd = self._build_windows_cmd(ffmpeg_path, output_path)
            else:
                logger.warning(f"不支持的平台: {sys.platform}")
                return False

            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            # On Windows, use DEVNULL for stdin (frag_keyframe handles graceful exit).
            # On macOS/Linux, SIGINT handles graceful exit via signal.
            self._ffmpeg_process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
            )
            # Give ffmpeg a moment to start up and detect any immediate errors
            time.sleep(0.5)
            retcode = self._ffmpeg_process.poll()
            if retcode is not None:
                # ffmpeg exited immediately — read error output
                stderr_output = self._ffmpeg_process.stderr.read().decode('utf-8', errors='replace')[:2000]
                logger.error(f"ffmpeg 进程启动后立即退出 (返回码={retcode}): {stderr_output}")
                self._ffmpeg_process = None
                return False
            logger.info(f"ffmpeg 进程已启动 (PID={self._ffmpeg_process.pid}, output={output_path})")
            return True
        except Exception as e:
            logger.error(f"启动 ffmpeg 失败: {e}")
            self._ffmpeg_process = None
            return False

    def _get_macos_input_device(self) -> str:
        """获取 macOS AVFoundation 输入设备标识。"""
        if self._macos_device:
            return self._macos_device
        # 默认值
        return "1:0"

    def _build_macos_cmd(self, ffmpeg_path: str, output_path: str) -> list[str]:
        """构建 macOS AVFoundation 录屏命令。"""
        device = self._get_macos_input_device()
        # 如果设备只有索引（如 "1"），不加 ":0" 音频部分
        # 如果已经是 "index:audio_index" 格式，直接使用
        return [
            ffmpeg_path,
            "-y",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "avfoundation",
            "-capture_cursor",
            "1",
            "-i",
            device,
            "-r",
            str(self._fps),
            "-vcodec",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "28",
            "-an",
            output_path,
        ]

    def _build_windows_cmd(self, ffmpeg_path: str, output_path: str) -> list[str]:
        """构建 Windows gdigrab 录屏命令。

        当设置了 region 时，会添加 crop 滤镜裁剪录制区域。
        """
        # -nostdin prevents ffmpeg from reading stdin (avoids crash on Windows).
        # -movflags +frag_keyframe writes fragmented MP4 — playable even if
        # the process is killed before writing the final moov atom.
        cmd = [
            ffmpeg_path,
            "-y",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "gdigrab",
            "-framerate",
            str(self._fps),
            "-i",
            "desktop",
            "-vcodec",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "28",
            "-movflags",
            "+frag_keyframe",
            "-an",
        ]

        # 如果设置了录制区域，添加 crop 滤镜
        # crop 格式: crop=width:height:x:y  (x,y 为左上角坐标)
        region = self._region
        if region:
            crop_w = region.get('width', 0)
            crop_h = region.get('height', 0)
            crop_x = region.get('left', 0)
            crop_y = region.get('top', 0)
            if crop_w > 0 and crop_h > 0:
                cmd.extend(["-vf", f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"])
                logger.info(
                    f"应用裁剪区域: crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
                )

        cmd.append(output_path)
        return cmd

    def _stop_ffmpeg(self, sigint_first: bool = True):
        """停止 ffmpeg 子进程。

        三阶段终止策略：
        1. SIGINT  → 优雅退出（ffmpeg 会完成编码并写出文件）
        2. SIGTERM → 强制终止
        3. SIGKILL → 彻底杀死

        Args:
            sigint_first: 是否先发送 SIGINT（确保文件完整写入）
        """
        proc = self._ffmpeg_process
        if proc is None:
            return
        try:
            pid = proc.pid

            # Stage 1: Graceful exit via SIGINT (Unix) — frag_keyframe on
            # Windows makes the file playable even without graceful exit.
            if sigint_first and sys.platform != "win32":
                try:
                    proc.send_signal(signal.SIGINT)
                    proc.wait(timeout=3)
                except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                    pass

            # Stage 2: SIGTERM / terminate
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                    pass

            # Stage 3: SIGKILL / kill
            if proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
                    pass

            logger.info(f"ffmpeg 进程已停止 (PID={pid})")
        except Exception as e:
            logger.warning(f"停止 ffmpeg 进程异常: {e}")
        finally:
            # Close pipes to avoid resource leaks
            if self._ffmpeg_process:
                for _pipe in (self._ffmpeg_process.stdin, self._ffmpeg_process.stderr):
                    if _pipe:
                        try:
                            _pipe.close()
                        except Exception:
                            pass
            self._ffmpeg_process = None

    # ---- 进程监控 ----

    def _start_monitor(self):
        """启动 ffmpeg 进程后台监控线程。"""
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True
        )
        self._monitor_thread.start()

    def _monitor_loop(self):
        """监控 ffmpeg 进程健康状态。

        如果 ffmpeg 在非暂停状态下意外退出，将自动停止录制并记录错误。
        """
        while True:
            with self._lock:
                if not self._running:
                    break
                proc = self._ffmpeg_process
                paused = self._paused

            if proc is None:
                time.sleep(1)
                continue

            retcode = proc.poll()
            if retcode is not None:
                with self._lock:
                    # Re-check running flag in case stop() was called concurrently
                    if not self._running:
                        break
                    if not self._paused:
                        logger.error(
                            f"ffmpeg 进程意外退出 (PID={proc.pid}, 返回码={retcode})"
                        )
                        self._running = False
                        self._ffmpeg_process = None
                        break
                    # Paused: ffmpeg was intentionally killed, just update reference
                    self._ffmpeg_process = None
            time.sleep(2)

    # ---- 分段录制 ----

    def _start_segment_timer(self):
        """启动分段切换定时器。"""
        self._stop_segment_timer()
        delay = self._segment_minutes * 60
        self._segment_timer = threading.Timer(delay, self._on_segment_timeout)
        self._segment_timer.daemon = True
        self._segment_timer.start()

    def _stop_segment_timer(self):
        """停止分段切换定时器。"""
        if self._segment_timer:
            self._segment_timer.cancel()
            self._segment_timer = None

    def _on_segment_timeout(self):
        """分段超时回调，在定时器线程中触发。"""
        threading.Thread(target=self._rotate_segment, daemon=True).start()

    def _rotate_segment(self):
        """切换到下一个分段文件。

        停止当前 ffmpeg 进程并启动一个新进程写入下一个分段文件。
        输出路径与 macOS 版本保持一致：outputs/<planName>/recording_XXX.mp4
        """
        with self._lock:
            if not self._running or self._paused:
                return
            try:
                logger.info(f"分段 {self._current_segment} 时间到，切换中")
                self._stop_ffmpeg(sigint_first=True)
                self._current_segment += 1

                self._output_path = self._build_output_path(self._current_segment)

                if not self._start_ffmpeg(self._output_path):
                    self._running = False
                    logger.error("分段切换失败，录制已停止")
                    return

                self._start_segment_timer()
                logger.info(f"分段已切换到 {self._current_segment}")
            except Exception as e:
                logger.error(f"分段切换异常: {e}")
