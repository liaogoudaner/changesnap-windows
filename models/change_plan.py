"""变更方案数据模型。

对应 PRD 中 JSON Schema 定义 (prd-data-model.md 1.1 节)。
解析后的方案数据统一为此结构。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Step:
    """单个步骤数据。"""
    seq: int
    description: str
    planned_time: str = ""
    implementer: str = ""
    reviewer: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'Step':
        return cls(
            seq=data.get('seq', 0),
            description=data.get('description', ''),
            planned_time=data.get('planned_time', ''),
            implementer=data.get('implementer', ''),
            reviewer=data.get('reviewer', ''),
        )


@dataclass
class StepGroup:
    """步骤分组。"""
    group_type: str  # pre_check / implementation / rollback / test / on_duty
    group_name: str = ""
    steps: list[Step] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'group_type': self.group_type,
            'group_name': self.group_name,
            'steps': [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'StepGroup':
        return cls(
            group_type=data.get('group_type', 'implementation'),
            group_name=data.get('group_name', ''),
            steps=[Step.from_dict(s) for s in data.get('steps', [])],
        )


@dataclass
class BasicInfo:
    """变更基本信息。"""
    product_name: str = ""
    author: str = ""
    plan_date: str = ""
    change_level: str = "一般"  # 重大 / 中等 / 一般
    remarks: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'BasicInfo':
        return cls(
            product_name=data.get('product_name', ''),
            author=data.get('author', ''),
            plan_date=data.get('plan_date', ''),
            change_level=data.get('change_level', '一般'),
            remarks=data.get('remarks', ''),
        )


@dataclass
class TimePersonnel:
    """变更时间与人员。"""
    change_time: str = ""
    product_contact: str = ""
    rd_contact: str = ""
    ops_contact: str = ""
    impl_contact: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'TimePersonnel':
        return cls(
            change_time=data.get('change_time', ''),
            product_contact=data.get('product_contact', ''),
            rd_contact=data.get('rd_contact', ''),
            ops_contact=data.get('ops_contact', ''),
            impl_contact=data.get('impl_contact', ''),
        )


@dataclass
class Sections:
    """方案中的文字段落。"""
    purpose: str = ""
    user_communication: str = ""
    current_status_overview: str = ""
    current_status_network: str = ""
    current_status_architecture: str = ""
    preparation: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'Sections':
        return cls(
            purpose=data.get('purpose', ''),
            user_communication=data.get('user_communication', ''),
            current_status_overview=data.get('current_status_overview', ''),
            current_status_network=data.get('current_status_network', ''),
            current_status_architecture=data.get('current_status_architecture', ''),
            preparation=data.get('preparation', ''),
        )


@dataclass
class Package:
    """代码包。"""
    version: str = ""
    pkg_type: str = ""  # 前端 / 后端 / 配置 / 其他
    name: str = ""
    md5: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return {
            'version': self.version,
            'type': self.pkg_type,
            'name': self.name,
            'md5': self.md5,
            'url': self.url,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Package':
        return cls(
            version=data.get('version', ''),
            pkg_type=data.get('type', data.get('pkg_type', '')),
            name=data.get('name', ''),
            md5=data.get('md5', ''),
            url=data.get('url', ''),
        )


@dataclass
class ChangePlan:
    """变更方案 — 统一数据模型。"""
    basic_info: BasicInfo = field(default_factory=BasicInfo)
    time_personnel: TimePersonnel = field(default_factory=TimePersonnel)
    sections: Sections = field(default_factory=Sections)
    step_groups: list[StepGroup] = field(default_factory=list)
    packages: list[Package] = field(default_factory=list)
    source_file: str = ""  # 原始文件路径
    source_format: str = ""  # excel / json / yaml

    def to_dict(self) -> dict:
        return {
            'basic_info': self.basic_info.to_dict(),
            'time_personnel': self.time_personnel.to_dict(),
            'sections': self.sections.to_dict() if self.sections else {},
            'step_groups': [g.to_dict() for g in self.step_groups],
            'packages': [p.to_dict() for p in self.packages],
            'source_file': self.source_file,
            'source_format': self.source_format,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ChangePlan':
        plan = cls(
            basic_info=BasicInfo.from_dict(data.get('basic_info', {})),
            time_personnel=TimePersonnel.from_dict(data.get('time_personnel', {})),
            sections=Sections.from_dict(data.get('sections', {})),
            step_groups=[StepGroup.from_dict(g) for g in data.get('step_groups', [])],
            packages=[Package.from_dict(p) for p in data.get('packages', [])],
            source_file=data.get('source_file', ''),
            source_format=data.get('source_format', ''),
        )
        return plan

    def deep_copy(self) -> 'ChangePlan':
        """深拷贝当前方案。"""
        return ChangePlan.from_dict(copy.deepcopy(self.to_dict()))

    def total_steps(self) -> int:
        """计算所有步骤的总数（不含 on_duty 组中的步骤）。"""
        count = 0
        for group in self.step_groups:
            if group.group_type != 'on_duty':
                count += len(group.steps)
        return count

    def get_step_by_seq(self, seq: int) -> tuple[Optional[StepGroup], Optional[Step]]:
        """根据序号查找步骤及其所属组。"""
        for group in self.step_groups:
            for step in group.steps:
                if step.seq == seq:
                    return group, step
        return None, None

    def get_steps_for_group_type(self, group_type: str) -> list[Step]:
        """获取指定类型的所有步骤。"""
        for group in self.step_groups:
            if group.group_type == group_type:
                return group.steps
        return []

    def get_step_count_by_type(self, group_type: str) -> int:
        """获取指定类型的步骤数量。"""
        return len(self.get_steps_for_group_type(group_type))
