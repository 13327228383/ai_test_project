# -*- coding: utf-8 -*-
"""
门禁3：持久化 Mixin
包含：persist_scores
"""

import logging
from src.core.guarded import guard
from typing import Any

_log = logging.getLogger(__name__)

from src.core.pipeline_config import pipeline_config
from ..constants import _GateConstantsMixin


class _GatePersistMixin(_GateConstantsMixin):
    """持久化 Mixin —— 将语义匹配分写入存储。"""

    def persist_scores(
        self, intents: list[dict[str, Any]], project_name: str = None
    ) -> list[dict[str, Any]]:
        """把语义匹配分写入 MySQL test_cases 表（或 excel_handler）。
        返回被标记为『需人工复核』的测试用例列表。"""
        need_review = []
        try:
            from src.core.excel_handler import excel_handler
        except Exception:
            excel_handler = None
        try:
            from src.pipeline.utils.case_store import case_store
        except Exception:
            case_store = None
        for intent in intents:
            score = intent.get("_semantic_score", 0.0)
            case_id = intent.get("case_id", "")
            title = intent.get("title", "")
            if (
                score > 0
                and score < pipeline_config.requirement_gate_top_score_threshold
            ):  # 低于阈值 → 需人工复核
                need_review.append({"case_id": case_id, "title": title, "score": score})
            # 写入 case_store 如果有 update_semantic_score(case_id, score)；如果没有就写到 excel_handler 的某个字段
            with guard("persist_scores: if case_store and hasattr(case_store", log=_log):
                if case_store and hasattr(case_store, "update_semantic_score"):
                    case_store.update_semantic_score(case_id, score, project_name)
                elif excel_handler and hasattr(excel_handler, "set_intent_field"):
                    excel_handler.set_intent_field(case_id, "_semantic_score", score)
        _log.info(
            "[Gate] 共 %d 条测试用例持久化语义分，%d 条需人工复核",
            len(intents),
            len(need_review),
        )
        return need_review
