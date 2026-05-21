"""会话数据模型。

对应 PRD prd-data-model.md 第 2 节。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional
from enum import Enum
import uuid

from models.change_plan import ChangePlan
from models.screenshot import ScreenshotMeta


# ----- 枚举定义 -----

class SessionStatus(str, Enum):
    """会话生命周期状态。"""
    PREPARING = "preparing"        # 加载方案中
    RUNNING = "running"            # 录屏+进行中
    PAUSED = "paused"              # 录制暂停
    REVIEWING = "reviewing"        # 已停止，预览阶段
    COMPLETED = "completed"        # 报告已生成，会话结束
    ABANDONED = "abandoned"        # 用户放弃的会话

    def __str__(self):
        return self.value


class StepStatus(str, Enum):
    """步骤状态。"""
    PENDING = "pending"            # 等待执行
    ACTIVE = "active"              # 当前高亮
    COMPLETED = "completed"        # 已完成
    SKIPPED = "skipped"            # 跳过

    def __str__(self):
        return self.value


class ScreenshotStatusEnum(str, Enum):
    """截图状态。"""
    ACTIVE = "active"              # 初始有效
    KEPT = "kept"                  # 确认保留
    DELETED = "deleted"            # 标记删除（软删除）
    ORPHAN = "orphan"              # 未关联步骤

    def __str__(self):
        return self.value


# ----- 会话步骤数据 -----

@dataclass
class SessionStep:
    """会话中的步骤状态，包含截图列表。"""
    step_id: str
    seq: int
    status: str = "pending"  # pending / active / completed / skipped
    planned_time: str = ""
    description: str = ""
    implementer: str = ""
    reviewer: str = ""
    actual_time: Optional[str] = None
    supplement: Optional[str] = None
    screenshots: list[ScreenshotMeta] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'step_id': self.step_id,
            'seq': self.seq,
            'status': self.status,
            'planned_time': self.planned_time,
            'description': self.description,
            'implementer': self.implementer,
            'reviewer': self.reviewer,
            'actual_time': self.actual_time,
            'supplement': self.supplement,
            'screenshots': [ss.to_dict() for ss in self.screenshots],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SessionStep':
        return cls(
            step_id=data.get('step_id', ''),
            seq=data.get('seq', 0),
            status=data.get('status', 'pending'),
            planned_time=data.get('planned_time', ''),
            description=data.get('description', ''),
            implementer=data.get('implementer', ''),
            reviewer=data.get('reviewer', ''),
            actual_time=data.get('actual_time'),
            supplement=data.get('supplement'),
            screenshots=[ScreenshotMeta.from_dict(ss) for ss in data.get('screenshots', [])],
        )


@dataclass
class SessionStepGroup:
    """会话中的步骤分组。"""
    group_type: str
    group_name: str = ""
    steps: list[SessionStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'group_type': self.group_type,
            'group_name': self.group_name,
            'steps': [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SessionStepGroup':
        return cls(
            group_type=data.get('group_type', ''),
            group_name=data.get('group_name', ''),
            steps=[SessionStep.from_dict(s) for s in data.get('steps', [])],
        )


@dataclass
class RecordingSegment:
    """录屏分段信息。"""
    segment_id: int
    filename: str
    start_time: str
    end_time: Optional[str] = None
    duration_seconds: Optional[float] = None
    file_size_bytes: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'RecordingSegment':
        return cls(
            segment_id=data.get('segment_id', 1),
            filename=data.get('filename', ''),
            start_time=data.get('start_time', ''),
            end_time=data.get('end_time'),
            duration_seconds=data.get('duration_seconds'),
            file_size_bytes=data.get('file_size_bytes'),
        )


@dataclass
class SessionSummary:
    """会话总结数据。"""
    overview: Optional[str] = None
    improvements: Optional[str] = None
    actual_implementer: Optional[str] = None
    actual_tester: Optional[str] = None
    actual_reviewer: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'SessionSummary':
        return cls(
            overview=data.get('overview'),
            improvements=data.get('improvements'),
            actual_implementer=data.get('actual_implementer'),
            actual_tester=data.get('actual_tester'),
            actual_reviewer=data.get('actual_reviewer'),
        )


# ----- 会话主状态 -----

@dataclass
class SessionState:
    """完整会话状态。"""
    session_id: str
    created_at: str
    updated_at: str
    status: str = SessionStatus.PREPARING
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    plan_file: dict = field(default_factory=lambda: {
        'original_path': '',
        'parsed': None,
    })

    recording: dict = field(default_factory=lambda: {
        'status': 'stopped',
        'segments': [],
    })

    current_step_id: Optional[str] = None
    settings: dict = field(default_factory=lambda: {
        'auto_advance': True,
        'screenshot_delay_ms': 200,
        'screenshot_monitor': 'primary',
    })

    step_groups: list[SessionStepGroup] = field(default_factory=list)
    orphan_screenshots: list[ScreenshotMeta] = field(default_factory=list)
    summary: SessionSummary = field(default_factory=SessionSummary)

    def to_dict(self) -> dict:
        return {
            'session_id': self.session_id,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'status': self.status,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'plan_file': self.plan_file,
            'recording': self.recording,
            'current_step_id': self.current_step_id,
            'settings': self.settings,
            'step_groups': [g.to_dict() for g in self.step_groups],
            'orphan_screenshots': [ss.to_dict() for ss in self.orphan_screenshots],
            'summary': self.summary.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SessionState':
        return cls(
            session_id=data.get('session_id', ''),
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
            status=data.get('status', SessionStatus.PREPARING),
            start_time=data.get('start_time'),
            end_time=data.get('end_time'),
            plan_file=data.get('plan_file', {'original_path': '', 'parsed': None}),
            recording=data.get('recording', {'status': 'stopped', 'segments': []}),
            current_step_id=data.get('current_step_id'),
            settings=data.get('settings', {
                'auto_advance': True,
                'screenshot_delay_ms': 200,
                'screenshot_monitor': 'primary',
            }),
            step_groups=[SessionStepGroup.from_dict(g) for g in data.get('step_groups', [])],
            orphan_screenshots=[ScreenshotMeta.from_dict(ss) for ss in data.get('orphan_screenshots', [])],
            summary=SessionSummary.from_dict(data.get('summary', {})),
        )

    @classmethod
    def create_new(cls, plan: ChangePlan) -> 'SessionState':
        """从 ChangePlan 创建新会话。"""
        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        session_id = str(uuid.uuid4())[:8]

        # 构建会话步骤分组
        step_groups = []
        global_seq = 0
        for group in plan.step_groups:
            session_steps = []
            for step in group.steps:
                global_seq += 1
                session_steps.append(SessionStep(
                    step_id=f"step-{global_seq:03d}",
                    seq=global_seq,
                    status='active' if global_seq == 1 else 'pending',
                    planned_time=step.planned_time,
                    description=step.description,
                    implementer=step.implementer,
                    reviewer=step.reviewer,
                    actual_time=None,
                    supplement=None,
                    screenshots=[],
                ))
            step_groups.append(SessionStepGroup(
                group_type=group.group_type,
                group_name=group.group_name,
                steps=session_steps,
            ))

        # 第一步骤为 active
        first_step_id = step_groups[0].steps[0].step_id if step_groups and step_groups[0].steps else None

        return cls(
            session_id=session_id,
            created_at=now,
            updated_at=now,
            status=SessionStatus.PREPARING,
            plan_file={
                'original_path': plan.source_file,
                'parsed': plan.to_dict(),
            },
            current_step_id=first_step_id,
            step_groups=step_groups,
        )

    def get_all_steps(self) -> list[SessionStep]:
        """获取所有步骤的扁平列表（缓存，步骤结构不变）。"""
        if not hasattr(self, '_all_steps_cache') or self._all_steps_cache is None:
            steps = []
            for group in self.step_groups:
                steps.extend(group.steps)
            self._all_steps_cache = steps
        return self._all_steps_cache

    def get_current_step(self) -> Optional[SessionStep]:
        """获取当前 ACTIVE 步骤。"""
        if not self.current_step_id:
            return None
        for step in self.get_all_steps():
            if step.step_id == self.current_step_id:
                return step
        return None

    def get_step_by_id(self, step_id: str) -> Optional[SessionStep]:
        """根据 step_id 查找步骤。"""
        for step in self.get_all_steps():
            if step.step_id == step_id:
                return step
        return None

    def get_step_group_for_step(self, step_id: str) -> Optional[SessionStepGroup]:
        """查找步骤所属的分组。"""
        for group in self.step_groups:
            for step in group.steps:
                if step.step_id == step_id:
                    return group
        return None

    @property
    def current_step_index(self) -> int:
        """当前步骤在全部步骤中的索引（0-based）。"""
        steps = self.get_all_steps()
        for i, step in enumerate(steps):
            if step.step_id == self.current_step_id:
                return i
        return 0

    @property
    def total_steps(self) -> int:
        """总步骤数（不含值守步骤）。"""
        count = 0
        for group in self.step_groups:
            if group.group_type != 'on_duty':
                count += len(group.steps)
        return count

    def get_screenshot_count(self) -> int:
        """获取所有截图总数。"""
        count = len(self.orphan_screenshots)
        for step in self.get_all_steps():
            count += len(step.screenshots)
        return count

    def set_step_status(self, step_id: str, status: str):
        """设置指定步骤的状态。"""
        step = self.get_step_by_id(step_id)
        if step:
            step.status = status

    def advance_to_next_step(self) -> bool:
        """自动前进到下一步。返回是否成功前进。"""
        all_steps = self.get_all_steps()
        if not self.current_step_id:
            return False

        current_idx = None
        for i, step in enumerate(all_steps):
            if step.step_id == self.current_step_id:
                current_idx = i
                step.status = StepStatus.COMPLETED
                break

        if current_idx is None:
            return False

        next_idx = current_idx + 1
        if next_idx < len(all_steps):
            next_step = all_steps[next_idx]
            next_step.status = StepStatus.ACTIVE
            self.current_step_id = next_step.step_id
            return True
        return False  # 已是最后一步

    def go_to_prev_step(self) -> bool:
        """回退到上一步。返回是否成功。"""
        all_steps = self.get_all_steps()
        if not self.current_step_id:
            return False

        current_idx = None
        for i, step in enumerate(all_steps):
            if step.step_id == self.current_step_id:
                current_idx = i
                step.status = StepStatus.PENDING
                break

        if current_idx is None or current_idx <= 0:
            return False

        prev_step = all_steps[current_idx - 1]
        prev_step.status = StepStatus.ACTIVE
        self.current_step_id = prev_step.step_id
        return True

    def jump_to_step(self, step_id: str) -> bool:
        """跳转到指定步骤。"""
        step = self.get_step_by_id(step_id)
        if not step:
            return False

        # 将当前 active 步骤恢复为 pending
        current = self.get_current_step()
        if current:
            current.status = StepStatus.PENDING

        step.status = StepStatus.ACTIVE
        self.current_step_id = step_id
        return True

    def skip_step(self, step_id: str) -> bool:
        """跳过指定步骤。"""
        step = self.get_step_by_id(step_id)
        if not step:
            return False
        step.status = StepStatus.SKIPPED
        self._auto_save()
        return True

    def complete_step(self, step_id: str) -> bool:
        """标记步骤为完成（无截图）。"""
        step = self.get_step_by_id(step_id)
        if not step:
            return False
        step.status = StepStatus.COMPLETED
        return self.advance_to_next_step()

    def add_screenshot(self, step_id: str, screenshot: ScreenshotMeta):
        """为指定步骤添加截图。"""
        if step_id == 'orphan':
            self.orphan_screenshots.append(screenshot)
            return
        step = self.get_step_by_id(step_id)
        if step:
            step.screenshots.append(screenshot)

    def move_screenshot(self, screenshot_id: str, target_step_id: str) -> bool:
        """将截图移动到目标步骤。"""
        # 从当前所在位置删除
        ss = self._remove_screenshot(screenshot_id)
        if not ss:
            return False
        ss.step_id = target_step_id
        ss.status = 'active'
        self.add_screenshot(target_step_id, ss)
        return True

    def _remove_screenshot(self, screenshot_id: str) -> Optional[ScreenshotMeta]:
        """从会话中移除截图并返回。"""
        # 检查所有步骤
        for step in self.get_all_steps():
            for i, ss in enumerate(step.screenshots):
                if ss.screenshot_id == screenshot_id:
                    return step.screenshots.pop(i)
        # 检查孤片
        for i, ss in enumerate(self.orphan_screenshots):
            if ss.screenshot_id == screenshot_id:
                return self.orphan_screenshots.pop(i)
        return None

    def delete_screenshot(self, screenshot_id: str) -> bool:
        """软删除截图。"""
        for step in self.get_all_steps():
            for ss in step.screenshots:
                if ss.screenshot_id == screenshot_id:
                    ss.status = 'deleted'
                    return True
        for ss in self.orphan_screenshots:
            if ss.screenshot_id == screenshot_id:
                ss.status = 'deleted'
                return True
        return False

    def restore_screenshot(self, screenshot_id: str) -> bool:
        """恢复已删除的截图。"""
        for step in self.get_all_steps():
            for ss in step.screenshots:
                if ss.screenshot_id == screenshot_id:
                    ss.status = 'active'
                    return True
        for ss in self.orphan_screenshots:
            if ss.screenshot_id == screenshot_id:
                ss.status = 'active'
                return True
        return False

    def set_screenshot_kept(self, screenshot_id: str, kept: bool) -> bool:
        """标记截图保留或取消保留。"""
        for step in self.get_all_steps():
            for ss in step.screenshots:
                if ss.screenshot_id == screenshot_id:
                    ss.status = 'kept' if kept else 'active'
                    return True
        for ss in self.orphan_screenshots:
            if ss.screenshot_id == screenshot_id:
                ss.status = 'kept' if kept else 'active'
                return True
        return False
