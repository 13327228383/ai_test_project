# -*- coding: utf-8 -*-
"""标准结构提取规则：提取阶段，匹配 **操作步骤** + **预期行为** 标准结构。"""

import re
from typing import Any
from .base import ExtractRule

# ── 标准结构正则 ──
_STANDARD_STRUCT_PATTERN = re.compile(
    r"(?:^|\n)(?:###\s+[^\n]+)?\s*"
    r"\*\*操作步骤\*\*[:：]?\s*\n"
    r"((?:(?:\d+\.\s+[^\n]+|[-*]\s+[^\n]+)\n?)+)"
    r"\s*\*\*预期行为\*\*[:：]?\s*\n"
    r"((?:(?:\d+\.\s+[^\n]+|[-*]\s+[^\n]+)\n?)+)",
    re.IGNORECASE,
)

# ── 前置条件正则 ──
_CONDITION_PATTERN = re.compile(
    r"\*\*前置条件\*\*[:：]?\s*\n((?:(?:\d+\.\s+[^\n]+|[-*]\s+[^\n]+)\n?)+)",
    re.IGNORECASE,
)


class StandardStructureRule(ExtractRule):
    """从需求文档标准结构提取三元组。

    匹配格式：
        **操作步骤**
        1. 点击新建按钮
        2. 输入品牌名称
        **预期行为**
        1. 页面跳转到新建页面
        2. 输入框显示输入内容

    每个操作+预期行为对 = 一个原子需求。
    """

    rule_id = "standard_structure"
    name = "标准结构提取"
    description = (
        "匹配需求文档中 **操作步骤** + **预期行为** 标准结构，提取原子需求三元组"
    )
    priority = 100

    def apply(self, chunk_text: str, chunk_id: str) -> list[dict[str, Any]]:
        if not chunk_text or not chunk_text.strip():
            return []

        # 匹配标准结构
        matches = list(_STANDARD_STRUCT_PATTERN.finditer(chunk_text))
        if not matches:
            return []

        # 提取前置条件
        cond_match = _CONDITION_PATTERN.search(chunk_text)
        condition = ""
        if cond_match:
            cond_lines = [
                self._strip_bullet(l)
                for l in cond_match.group(1).strip().split("\n")
                if l.strip()
            ]
            condition = "; ".join(cond_lines) if cond_lines else ""

        triples = []
        for m in matches:
            steps_text = m.group(1).strip()
            expected_text = m.group(2).strip()

            step_lines = [x for l in steps_text.split("\n") if (x := l.strip())]
            expected_lines = [x for l in expected_text.split("\n") if (x := l.strip())]

            operations = [
                self._strip_bullet(sl) for sl in step_lines if self._strip_bullet(sl)
            ]
            expectations = [
                self._strip_bullet(el)
                for el in expected_lines
                if self._strip_bullet(el)
            ]

            if not operations:
                continue

            max_len = max(len(operations), len(expectations))
            for i in range(max_len):
                op = operations[i] if i < len(operations) else operations[-1]
                exp = (
                    expectations[i]
                    if i < len(expectations)
                    else (expectations[-1] if expectations else "")
                )
                source_quote = f"**操作步骤**: {op}"

                triples.append(
                    {
                        "operation": op,
                        "condition": condition,
                        "expected_feedback": exp,
                        "source_quote": source_quote,
                        "source_section": chunk_id,
                        "confidence": 0.90,
                        "extraction_method": "rule",
                        "is_implicit": 0,
                    }
                )

        return triples
