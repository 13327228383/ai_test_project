# -*- coding: utf-8 -*-
"""★ T04.1: VLM 视觉校验统一协议 [已复核-DeepSeek-V3 2026-08-16]
状态/结果/适配器 ABC，与 _verify_visual.py STATUS_* 三态语义对齐。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class VisualStatus(str, Enum):
    """视觉校验终态（与 _verify_visual.py STATUS_* 对齐，fail-closed）。"""

    PASS = "PASS"
    FAIL = "FAIL"
    NO_DATA = "NO_DATA"


@dataclass
class VisualVerdict:
    """视觉校验结果（后端无关统一产出）。"""

    status: VisualStatus
    confidence: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    backend_name: str = ""

    def is_conclusive(self) -> bool:
        """是否给出确定结论（PASS/FAIL），区别于 NO_DATA。"""
        return self.status in (VisualStatus.PASS, VisualStatus.FAIL)


class VLMAdapter(ABC):
    """视觉校验后端抽象基类。"""

    name: str = ""
    priority: int = 100

    @abstractmethod
    def is_available(self) -> bool:
        """后端当前是否可用（调用前预检）。"""

    @abstractmethod
    def verify(
        self,
        screenshot_bytes: Optional[bytes],
        assertion: str,
        context: dict[str, Any],
    ) -> VisualVerdict:
        """执行视觉校验；无法判定时返回 NO_DATA，绝不伪造 PASS。"""
