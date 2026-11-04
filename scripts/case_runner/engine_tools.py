# -*- coding: utf-8 -*-
"""引擎真实工具全集（权威事实源 = 引擎自己执行分派表，非本文件硬编码）。

为什么不用 CHECK_TAXONOMY 单独判定：
  实测用例集里的指令名（如 `verify_element_exists`）并不总等于 CHECK_TAXONOMY
  的 tool_name（`verify_element_exists_check`），但引擎分派表两者都登记了。
  若只按注册表匹配，会把引擎实际支持的指令误判为缺口（驱动器假报，冤枉引擎）。

因此本模块用 AST 从 `_single.py::_execute_tool` 提取 `_TOOL_HANDLERS` 的键集合，
即「引擎真实可下发工具」的唯一权威来源；新工具随引擎新增自动可见，无需改本文件。
"""
from __future__ import annotations

import ast
import os
import threading
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SINGLE = os.path.join(
    _ROOT,
    "src",
    "pipeline",
    "steps",
    "step4",
    "browser_tools",
    "execution",
    "_single.py",
)

_cache: set[str] | None = None
_lock = threading.Lock()


def _extract() -> set[str]:
    with open(_SINGLE, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    names: set[str] = set()
    for node in ast.walk(tree):
        # _TOOL_HANDLERS 是 _execute_tool 内的局部 dict
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_TOOL_HANDLERS":
                    if isinstance(node.value, ast.Dict):
                        for k in node.value.keys:
                            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                names.add(k.value)
    return names


def engine_tool_names(force: bool = False) -> set[str]:
    """引擎真实可下发的工具名全集。"""
    global _cache
    with _lock:
        if _cache is None or force:
            _cache = _extract()
        return set(_cache)


def is_engine_tool(name: str) -> bool:
    return (name or "").strip() in engine_tool_names()
