# -*- coding: utf-8 -*-
from __future__ import annotations

"""
工具辅助查询
============
- 根据工具名查找 schema
- 返回所有工具名列表
- 返回完整 schema 列表
"""

from typing import Any

from src.pipeline.steps.step4.browser_tools.schemas import ALL_TOOLS


def get_tool_schema(name: str) -> dict[str, Any] | None:
    """根据 tool_name 查找 schema。"""
    for t in ALL_TOOLS:
        fn = t.get("function", {})
        if fn.get("name") == name:
            return t
    return None


def tool_name_list() -> list[str]:
    """返回所有合法的工具名。"""
    names = []
    for t in ALL_TOOLS:
        names.append(t["function"]["name"])
    return sorted(names)


def build_tool_schemas() -> list[dict[str, Any]]:
    """返回所有工具的完整 schema 列表（直接喂给支持 function_calling 的模型）。"""
    return list(ALL_TOOLS)
