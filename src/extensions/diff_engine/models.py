# -*- coding: utf-8 -*-
"""DiffEngine / ImpactAnalyzer 的数据模型 — 从 diff_engine.py 拆出，零内容变更。"""

from typing import Any
from dataclasses import dataclass, field


@dataclass
class DiffEntry:
    """单条变更记录。"""

    entity_type: str  # requirement | api_endpoint | db_table | db_column
    entity_id: str  # REQ-001 | POST:/api/x | BRAND_INFO | BRAND_INFO.BRAND_CODE
    change_type: str  # added | removed | modified
    old_value: Any = None
    new_value: Any = None
    details: str = ""  # 人类可读的变更描述

    @property
    def severity(self) -> str:
        """★ KEEP-18: 自动计算变更严重度。
        removed > requirement/modified > db_table/modified > api_endpoint/added > 其他。
        """
        if self.change_type == "removed":
            return "high"
        if self.change_type == "modified":
            if self.entity_type == "requirement":
                return "high"
            if self.entity_type in ("db_table", "db_column"):
                return "medium"
            return "medium"
        if self.change_type == "added":
            if self.entity_type in ("db_table", "db_column"):
                return "low"
            return "low"
        return "low"


@dataclass
class DiffReport:
    """变更对比报告。"""

    timestamp: str = ""
    entries: list[DiffEntry] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return len(self.entries) > 0

    @property
    def added_count(self) -> int:
        return sum(1 for e in self.entries if e.change_type == "added")

    @property
    def removed_count(self) -> int:
        return sum(1 for e in self.entries if e.change_type == "removed")

    @property
    def modified_count(self) -> int:
        return sum(1 for e in self.entries if e.change_type == "modified")

    @property
    def high_severity_count(self) -> int:
        """★ KEEP-18: 高严重度变更数。"""
        return sum(1 for e in self.entries if e.severity == "high")

    @property
    def medium_severity_count(self) -> int:
        """★ KEEP-18: 中严重度变更数。"""
        return sum(1 for e in self.entries if e.severity == "medium")

    @property
    def low_severity_count(self) -> int:
        """★ KEEP-18: 低严重度变更数。"""
        return sum(1 for e in self.entries if e.severity == "low")


@dataclass
class ImpactResult:
    """影响分析结果。"""

    affected_requirements: list[str] = field(default_factory=list)
    affected_test_cases: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)
    severity: str = "low"  # low | medium | high
