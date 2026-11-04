# -*- coding: utf-8 -*-
"""条件句式提取规则：提取阶段，匹配"当...则..."等条件句式。"""

import re
from typing import Any
from .base import ExtractRule

# ── 条件句式正则（中英文）──
# 匹配：当...则...、如果...那么...、若...则...、when...then...
_CONDITIONAL_PATTERN = re.compile(
    r"(?:当|如果|若|假如|一旦|when|if)\s*[:：]?\s*([^，。,.\n]+?)(?:[，,。.]\s*)?(?:则|那么|就|便|then)\s*[:：]?\s*([^。\n]+)",
    re.IGNORECASE,
)

# ── 前置条件正则 ──
_CONDITION_PATTERN = re.compile(
    r"\*\*前置条件\*\*[:：]?\s*\n((?:(?:\d+\.\s+[^\n]+|[-*]\s+[^\n]+)\n?)+)",
    re.IGNORECASE,
)


class ConditionalStatementRule(ExtractRule):
    """从条件句式中提取三元组。

    匹配格式：
        - 当用户点击提交按钮，则系统校验表单数据
        - 如果输入框为空，那么显示错误提示
        - 若权限不足，则拒绝访问
        - When the user clicks submit, then the system validates the form

    每个条件+结果 = 一个原子需求。
    """

    rule_id = "conditional_statement"
    name = "条件句式提取"
    description = "匹配'当...则...'、'如果...那么...'等条件句式，提取原子需求三元组"
    priority = 70

    def apply(self, chunk_text: str, chunk_id: str) -> list[dict[str, Any]]:
        if not chunk_text or not chunk_text.strip():
            return []

        # 匹配条件句式
        matches = list(_CONDITIONAL_PATTERN.finditer(chunk_text))
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
            condition_text = m.group(1).strip()
            result_text = m.group(2).strip()

            # 构造 operation（条件部分）
            operation = f"当{condition_text}"

            # 构造 expected_feedback（结果部分）
            expected_feedback = result_text

            # 构造 source_quote（原文引用）
            source_quote = m.group(0).strip()

            triples.append(
                {
                    "operation": operation,
                    "condition": condition,
                    "expected_feedback": expected_feedback,
                    "source_quote": source_quote,
                    "source_section": chunk_id,
                    "confidence": 0.85,
                    "extraction_method": "rule",
                    "is_implicit": 0,
                }
            )

        return triples
