"""截图元数据模型。

对应 PRD prd-data-model.md 第 5 节。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class ScreenshotMeta:
    """单张截图的元数据。"""
    screenshot_id: str
    filename: str
    timestamp: str  # ISO 格式时间戳
    step_id: str = ""
    seq: int = 0
    width: int = 0
    height: int = 0
    file_size_bytes: int = 0
    md5: str = ""
    monitor_index: int = 0
    status: str = "active"  # active / kept / deleted / orphan
    taken_while_paused: bool = False
    filepath: str = ""  # 绝对路径

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'ScreenshotMeta':
        return cls(
            screenshot_id=data.get('screenshot_id', ''),
            filename=data.get('filename', ''),
            timestamp=data.get('timestamp', ''),
            step_id=data.get('step_id', ''),
            seq=data.get('seq', 0),
            width=data.get('width', 0),
            height=data.get('height', 0),
            file_size_bytes=data.get('file_size_bytes', 0),
            md5=data.get('md5', ''),
            monitor_index=data.get('monitor_index', 0),
            status=data.get('status', 'active'),
            taken_while_paused=data.get('taken_while_paused', False),
            filepath=data.get('filepath', ''),
        )

    @property
    def is_kept(self) -> bool:
        return self.status == 'kept'

    @property
    def is_deleted(self) -> bool:
        return self.status == 'deleted'

    @property
    def is_orphan(self) -> bool:
        return self.status == 'orphan'
