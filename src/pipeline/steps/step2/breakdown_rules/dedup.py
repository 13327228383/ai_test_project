# -*- coding: utf-8 -*-
"""去重合并规则：清洗阶段，按 (operation, expected_feedback) 去重。"""

from typing import Any
from .base import CleanRule


class DedupRule(CleanRule):
    """按 (operation, expected_feedback) 去重合并三元组。

    相同操作+预期反馈 → 合并为一条，保留最高置信度。
    """

    rule_id = "dedup"
    name = "去重合并"
    description = "按 (operation, expected_feedback) 去重合并，保留最高置信度"
    priority = 100

    def apply(self, triples: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not triples:
            return []

        seen = {}
        for t in triples:
            key = (
                (t.get("operation") or "").strip(),
                (t.get("expected_feedback") or "").strip(),
            )
            if key in seen:
                # 保留置信度更高的
                if t.get("confidence", 0) > seen[key].get("confidence", 0):
                    seen[key] = t
            else:
                seen[key] = t

        return list(seen.values())
