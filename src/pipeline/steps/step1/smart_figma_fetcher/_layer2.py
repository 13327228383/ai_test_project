# -*- coding: utf-8 -*-
"""Layer 2: 全量保留 — 不再做 AI 筛选（原红蓝对抗已移除）。

Step1 拉取的所有 Figma 页面全部保留，按需检索由 Step2/3 通过 RAG 完成。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class _Layer2Mixin:
    """Layer 2: 全量保留 Figma 页面（零 AI 调用）。"""

    def _ai_filter_pages(self, pages: list[dict]) -> dict[str, Any]:
        """全量保留所有页面。Step2/3 通过 RAG 按需检索，不再在 Step1 做 AI 筛选。"""
        if not pages:
            return {"filtered_node_ids": [], "excluded_node_ids": [], "ai_analysis": ""}

        all_ids = [p["id"] for p in pages if p["id"]]
        _log.info(
            "[Layer 2] 全量保留 %d 个页面（红蓝对抗已移除，按需检索由 RAG 完成）",
            len(all_ids),
        )

        return {
            "filtered_node_ids": all_ids,
            "excluded_node_ids": [],
            "ai_analysis": "全量保留 (%d 页面)" % len(all_ids),
        }
