# -*- coding: utf-8 -*-
from __future__ import annotations

"""
测试规则知识库 (TestRulesKB)
=============================
加载测试规则 JSON，注入到验证意图生成 prompt 中作为"护栏"。

规则来源：test_rules.json（内置默认 + 项目级覆盖）
项目级路径: 智能分析/系统导航/test_rules.json

【2026-06-18 重构】原 474 行单文件按职责拆分：
- default_rules.py — 内置 DEFAULT_RULES 常量（含 7 个 patterns + global_constraints）
- loader.py       — _LoaderMixin（rules_path / _ensure_loaded / save_patterns）
- matching.py     — _MatchingMixin（match_rules / get_hit_stats / flush_hit_stats_to_metrics）
- formatting.py   — _FormattingMixin（get_forbidden_terms / format_forbidden_context / format_context）
"""

from collections import Counter

from .default_rules import DEFAULT_RULES
from .loader import _LoaderMixin, MAX_AUTO_DISCOVERED_RULES_PER_PROJECT
from .matching import _MatchingMixin, MAX_CANDIDATE_RULES_PER_REQUIREMENT
from .formatting import (
    _FormattingMixin,
    MAX_INJECTED_RULES_PER_REQUIREMENT,
    MAX_RULE_CONTEXT_CHARS,
    MAX_MUST_COVER_PER_RULE,
    MAX_COMMON_BUGS_PER_RULE,
)


class TestRulesKB(_LoaderMixin, _MatchingMixin, _FormattingMixin):
    """测试规则知识库 — 单例。"""

    def __init__(self):
        self._rules: dict | None = None
        self._project_rules: dict | None = None
        # ★ KEEP-16: 规则命中率统计
        self._hit_stats: dict[str, int] = Counter()
        self._total_match_calls: int = 0


# 单例
# ★ 方案C 改造：test_rules_kb 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

test_rules_kb = PerProjectRegistry(TestRulesKB)
