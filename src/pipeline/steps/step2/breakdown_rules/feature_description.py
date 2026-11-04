# -*- coding: utf-8 -*-
"""功能描述提取规则：提取阶段，匹配"支持XX功能"、"提供XX能力"等功能描述句式。"""

import re
from typing import Any
from .base import ExtractRule

# ── 功能描述句式正则（中英文）──
# 匹配：支持XX功能、提供XX能力、具备XX特性、实现XX功能、包含XX功能
_FEATURE_PATTERN = re.compile(
    r"(?:支持|提供|具备|实现|包含|拥有|集成|内置)\s*[:：]?\s*([^，。,.\n]+?)(?:功能|能力|特性|特性|接口|API|服务|模块|组件)",
    re.IGNORECASE,
)

# ── 前置条件正则 ──
_CONDITION_PATTERN = re.compile(
    r"\*\*前置条件\*\*[:：]?\s*\n((?:(?:\d+\.\s+[^\n]+|[-*]\s+[^\n]+)\n?)+)",
    re.IGNORECASE,
)


class FeatureDescriptionRule(ExtractRule):
    """从功能描述句式中提取三元组。

    匹配格式：
        - 支持用户权限管理功能
        - 提供数据导出能力
        - 具备日志审计特性
        - 实现批量处理功能
        - Support user permission management feature
        - Provide data export capability

    每个功能描述 = 一个原子需求。
    """

    rule_id = "feature_description"
    name = "功能描述提取"
    description = "匹配'支持XX功能'、'提供XX能力'等功能描述句式，提取原子需求三元组"
    priority = 60

    def apply(self, chunk_text: str, chunk_id: str) -> list[dict[str, Any]]:
        if not chunk_text or not chunk_text.strip():
            return []

        # 匹配功能描述句式
        matches = list(_FEATURE_PATTERN.finditer(chunk_text))
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
            feature_text = m.group(1).strip()

            # 构造 operation（功能描述）
            operation = f"使用{feature_text}功能"

            # 构造 expected_feedback（预期反馈）
            expected_feedback = f"{feature_text}功能正常可用"

            # 构造 source_quote（原文引用）
            source_quote = m.group(0).strip()

            triples.append(
                {
                    "operation": operation,
                    "condition": condition,
                    "expected_feedback": expected_feedback,
                    "source_quote": source_quote,
                    "source_section": chunk_id,
                    "confidence": 0.75,
                    "extraction_method": "rule",
                    "is_implicit": 0,
                }
            )

        return triples
