# -*- coding: utf-8 -*-
"""
browser_intent_parser — 测试用例解析器
==========================================
从 browser_core.py 拆分出的独立模块，负责测试用例的结构解析、
意图折叠、capability_hint 推断、读写分区等纯逻辑。

★ 安全拆分原则：
  - 零业务逻辑变更，仅物理位置移动
  - 保持所有导入路径、函数签名、返回值 100% 一致
  - 原文件通过门面模式兼容
"""

# re-export 所有公开函数和常量
from ._io import (
    extract_intent_schema,
    has_write_keyword,
    partition_intents_by_io,
    intent_needs_login,
    _WRITE_KEYWORDS,
)
from ._steps import (
    normalize_steps,
    steps_from_case,
    collapse_steps_to_case,
    format_steps,
)

__all__ = [
    "extract_intent_schema",
    "has_write_keyword",
    "partition_intents_by_io",
    "intent_needs_login",
    "_WRITE_KEYWORDS",
    "normalize_steps",
    "steps_from_case",
    "collapse_steps_to_case",
    "format_steps",
]
