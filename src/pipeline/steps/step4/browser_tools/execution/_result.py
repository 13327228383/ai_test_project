# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Tool 执行结果封装
=================
"""

from typing import Any


class ToolExecutionResult:
    """工具执行结果（成功/失败 + 内容）。"""

    def __init__(self, ok: bool, content: str, extra: dict[str, Any] | None = None):
        self.ok = bool(ok)
        self.content = content
        self.extra = extra or {}

    def to_dict(self) -> dict[str, Any]:
        status_str = "success" if self.ok else "failure"
        result = {"status": status_str, "result": self.content[:2000]}
        if self.extra:
            for k, v in self.extra.items():
                result[k] = v
        return result
