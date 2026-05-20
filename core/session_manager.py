"""会话管理模块。

管理会话的创建、持久化、恢复、清理。
会话状态保存为 JSON 文件到 ~/.session/{session_id}.json。
"""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from models.change_plan import ChangePlan
from models.session import (
    SessionState, SessionStatus, StepStatus,
    SessionStep, SessionStepGroup, SessionSummary,
)
from models.screenshot import ScreenshotMeta
from utils.file_utils import (
    ensure_dir, get_session_dir, get_screenshots_dir, get_recordings_dir,
    get_work_dir, get_file_size,
)
from utils.log_utils import get_logger

logger = get_logger(__name__)


class SessionManager:
    """会话管理器。"""

    def __init__(self):
        self._session: Optional[SessionState] = None
        self._lock = threading.Lock()
        self._auto_save_timer: Optional[threading.Timer] = None
        self._dirty = False
        self._initialized = False

    # ---- 属性 ----

    @property
    def current_session(self) -> Optional[SessionState]:
        return self._session

    @property
    def is_active(self) -> bool:
        return self._session is not None and self._session.status not in (
            SessionStatus.COMPLETED, SessionStatus.ABANDONED,
        )

    @property
    def is_running(self) -> bool:
        return self._session is not None and self._session.status == SessionStatus.RUNNING

    # ---- 会话创建 ----

    def create_session(self, plan: ChangePlan) -> SessionState:
        """从变更方案创建新会话。"""
        self._cleanup_auto_save()
        self._session = SessionState.create_new(plan)
        self._session.status = SessionStatus.RUNNING
        self._session.start_time = datetime.now(timezone(timedelta(hours=8))).isoformat()
        self._save()
        self._start_auto_save()
        self._initialized = True
        logger.info(f"会话已创建: {self._session.session_id}")
        return self._session

    def start_session(self):
        """将会话从 preparing 切换到 running。"""
        if self._session:
            self._session.status = SessionStatus.RUNNING
            self._session.start_time = datetime.now(timezone(timedelta(hours=8))).isoformat()
            self._save()
            logger.info(f"会话已开始: {self._session.session_id}")

    # ---- 会话持久化 ----

    def save(self):
        """强制保存当前会话。"""
        if self._session:
            self._save()

    def _save(self):
        """内部保存。"""
        if not self._session:
            return
        try:
            self._session.updated_at = datetime.now(timezone(timedelta(hours=8))).isoformat()
            session_dir = get_session_dir()
            filepath = session_dir / f"{self._session.session_id}.json"
            bak_filepath = session_dir / f"{self._session.session_id}.json.bak"

            data = self._session.to_dict()

            # 先写备份，再写主文件
            with open(bak_filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            self._dirty = False
        except Exception as e:
            logger.error(f"会话保存失败: {e}")

    def _start_auto_save(self):
        """启动自动保存定时器（每 60 秒）。"""
        self._stop_auto_save()
        self._auto_save_timer = threading.Timer(60.0, self._auto_save_tick)
        self._auto_save_timer.daemon = True
        self._auto_save_timer.start()

    def _stop_auto_save(self):
        """停止自动保存定时器。"""
        if self._auto_save_timer:
            self._auto_save_timer.cancel()
            self._auto_save_timer = None

    def _auto_save_tick(self):
        """自动保存回调。"""
        if self._session and self._session.status in (
            SessionStatus.RUNNING, SessionStatus.PAUSED, SessionStatus.REVIEWING,
        ):
            self._save()
            self._start_auto_save()

    def _cleanup_auto_save(self):
        """清理自动保存。"""
        self._stop_auto_save()

    # ---- 会话恢复 ----

    def find_incomplete_sessions(self) -> list[dict]:
        """查找所有未完成的会话。"""
        session_dir = get_session_dir()
        if not session_dir.exists():
            return []

        sessions = []
        for f in sorted(session_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.name.endswith('.bak'):
                continue
            try:
                with open(f, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                status = data.get('status', '')
                if status in ('running', 'paused', 'reviewing', 'preparing'):
                    plan_file = data.get('plan_file', {})
                    parsed = plan_file.get('parsed', {}) or {}
                    basic_info = parsed.get('basic_info', {}) if isinstance(parsed, dict) else {}

                    sessions.append({
                        'session_id': data.get('session_id', ''),
                        'status': status,
                        'updated_at': data.get('updated_at', ''),
                        'created_at': data.get('created_at', ''),
                        'product_name': basic_info.get('product_name', '未知方案') if isinstance(basic_info, dict) else '未知方案',
                        'step_count': len(data.get('step_groups', [])),
                    })
            except Exception as e:
                logger.warning(f"会话文件读取失败: {f.name}: {e}")

        return sessions

    def load_session(self, session_id: str) -> Optional[SessionState]:
        """加载指定会话。"""
        session_dir = get_session_dir()
        filepath = session_dir / f"{session_id}.json"
        bak_filepath = session_dir / f"{session_id}.json.bak"

        # 先尝试主文件，再尝试备份
        for fp in [filepath, bak_filepath]:
            if fp.exists():
                try:
                    with open(fp, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self._session = SessionState.from_dict(data)
                    self._initialized = True
                    self._start_auto_save()
                    logger.info(f"会话已加载: {session_id}")
                    return self._session
                except Exception as e:
                    logger.error(f"会话加载失败: {fp.name}: {e}")

        logger.warning(f"会话文件未找到: {session_id}")
        return None

    def mark_abandoned(self, session_id: str):
        """标记会话为放弃。"""
        session = self.load_session(session_id)
        if session:
            session.status = SessionStatus.ABANDONED
            self._session = session
            self._save()
            self._session = None
            self._cleanup_auto_save()

    def mark_completed(self):
        """标记会话为已完成。"""
        if self._session:
            self._session.status = SessionStatus.COMPLETED
            self._session.end_time = datetime.now(timezone(timedelta(hours=8))).isoformat()
            self._save()
            self._cleanup_auto_save()
            logger.info(f"会话已完成: {self._session.session_id}")

    def set_reviewing(self):
        """切换到预览阶段。"""
        if self._session:
            self._session.status = SessionStatus.REVIEWING
            self._session.end_time = datetime.now(timezone(timedelta(hours=8))).isoformat()
            self._save()

    # ---- 步骤操作 ----

    def set_current_step(self, step_id: str):
        """设置当前步骤并保存。"""
        if not self._session:
            return
        self._session.current_step_id = step_id
        self._mark_dirty()

    def advance_step(self) -> bool:
        """前进到下一步。"""
        if not self._session:
            return False
        result = self._session.advance_to_next_step()
        if result:
            self._mark_dirty()
        return result

    def prev_step(self) -> bool:
        """回退到上一步。"""
        if not self._session:
            return False
        result = self._session.go_to_prev_step()
        if result:
            self._mark_dirty()
        return result

    def jump_to_step(self, step_id: str) -> bool:
        """跳转到指定步骤。"""
        if not self._session:
            return False
        result = self._session.jump_to_step(step_id)
        if result:
            self._mark_dirty()
        return result

    def skip_step(self, step_id: str) -> bool:
        """跳过步骤。"""
        if not self._session:
            return False
        result = self._session.skip_step(step_id)
        if result:
            self._mark_dirty()
        return result

    def complete_step(self, step_id: str) -> bool:
        """标记步骤完成。"""
        if not self._session:
            return False
        result = self._session.complete_step(step_id)
        if result:
            self._mark_dirty()
        return result

    # ---- 截图操作 ----

    def add_screenshot(self, step_id: str, screenshot: ScreenshotMeta):
        """为步骤添加截图。"""
        if not self._session:
            return
        self._session.add_screenshot(step_id, screenshot)
        self._mark_dirty()
        # 截图后立即保存
        self._save()

    def delete_screenshot(self, screenshot_id: str) -> bool:
        """软删除截图。"""
        if not self._session:
            return False
        result = self._session.delete_screenshot(screenshot_id)
        if result:
            self._mark_dirty()
        return result

    def restore_screenshot(self, screenshot_id: str) -> bool:
        """恢复已删除截图。"""
        if not self._session:
            return False
        result = self._session.restore_screenshot(screenshot_id)
        if result:
            self._mark_dirty()
        return result

    def set_screenshot_kept(self, screenshot_id: str, kept: bool) -> bool:
        """设置截图保留状态。"""
        if not self._session:
            return False
        result = self._session.set_screenshot_kept(screenshot_id, kept)
        if result:
            self._mark_dirty()
        return result

    def move_screenshot(self, screenshot_id: str, target_step_id: str) -> bool:
        """移动截图到目标步骤。"""
        if not self._session:
            return False
        result = self._session.move_screenshot(screenshot_id, target_step_id)
        if result:
            self._mark_dirty()
        return result

    # ---- 补充数据 ----

    def update_step_supplement(self, step_id: str, supplement: str):
        """更新步骤补充说明。"""
        if not self._session:
            return
        step = self._session.get_step_by_id(step_id)
        if step:
            step.supplement = supplement
            self._mark_dirty()

    def update_step_actual_time(self, step_id: str, actual_time: str):
        """更新步骤实际执行时间。"""
        if not self._session:
            return
        step = self._session.get_step_by_id(step_id)
        if step:
            step.actual_time = actual_time
            self._mark_dirty()

    def update_summary(self, summary: SessionSummary):
        """更新总结数据。"""
        if not self._session:
            return
        self._session.summary = summary
        self._mark_dirty()

    # ---- 会话设置 ----

    def get_setting(self, key: str, default=None):
        if not self._session:
            return default
        return self._session.settings.get(key, default)

    def set_setting(self, key: str, value):
        if self._session:
            self._session.settings[key] = value
            self._mark_dirty()

    # ---- 录屏状态 ----

    def init_recording(self, segment_id: int = 1):
        """初始化录屏记录。"""
        if not self._session:
            return
        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        self._session.recording = {
            'status': 'recording',
            'segments': [{
                'segment_id': segment_id,
                'filename': f'recording_{segment_id:03d}.mp4',
                'start_time': now,
                'end_time': None,
                'duration_seconds': None,
                'file_size_bytes': None,
            }],
        }
        self._mark_dirty()

    def pause_recording(self):
        """暂停录屏。"""
        if not self._session:
            return
        if not self._session.recording:
            logger.warning("pause_recording: 录屏未初始化，跳过")
            return
        self._session.recording['status'] = 'paused'
        self._session.status = SessionStatus.PAUSED
        self._mark_dirty()

    def resume_recording(self):
        """恢复录屏。"""
        if not self._session:
            return
        if not self._session.recording:
            logger.warning("resume_recording: 录屏未初始化，跳过")
            return
        self._session.recording['status'] = 'recording'
        self._session.status = SessionStatus.RUNNING
        # 新增分段
        segments = self._session.recording.get('segments', [])
        new_segment_id = len(segments) + 1
        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        segments.append({
            'segment_id': new_segment_id,
            'filename': f'recording_{new_segment_id:03d}.mp4',
            'start_time': now,
            'end_time': None,
            'duration_seconds': None,
            'file_size_bytes': None,
        })
        self._session.recording['segments'] = segments
        self._mark_dirty()
        return new_segment_id

    def finalize_recording(self):
        """完成录屏记录。"""
        if not self._session:
            return
        if not self._session.recording:
            logger.warning("finalize_recording: 录屏未初始化，跳过")
            return
        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        self._session.recording['status'] = 'stopped'
        for seg in self._session.recording.get('segments', []):
            if seg.get('end_time') is None:
                seg['end_time'] = now
        self._mark_dirty()

    def add_recording_segment(self, segment_id: int, filename: str):
        """新增录屏分段。"""
        if not self._session:
            return
        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        segments = self._session.recording.get('segments', [])
        # 关闭前一段
        if segments:
            segments[-1]['end_time'] = now
        segments.append({
            'segment_id': segment_id,
            'filename': filename,
            'start_time': now,
            'end_time': None,
            'duration_seconds': None,
            'file_size_bytes': None,
        })
        self._session.recording['segments'] = segments
        self._mark_dirty()

    def update_step_supplement_by_seq(self, seq: int, text: str):
        """按序号更新步骤补充说明。"""
        if not self._session:
            return
        for group in self._session.step_groups:
            for step in group.steps:
                if step.seq == seq:
                    step.supplement = text
                    self._mark_dirty()
                    return

    # ---- 内部 ----

    def _mark_dirty(self):
        """标记为脏数据。"""
        self._dirty = True

    def get_session_dict(self) -> dict:
        """获取会话字典（用于 API 响应）。"""
        if not self._session:
            return {}
        return self._session.to_dict()

    def destroy(self):
        """销毁会话管理器。"""
        self._cleanup_auto_save()
        if self._session:
            self._save()
        self._session = None
