# -*- coding: utf-8 -*-
"""插件指标埋点（capability / fuzzy_match / dom_sizer / region_tree / click_fallback / dom_diff）— 从 metrics_collector.py 拆出，零业务逻辑变更。"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


class _PluginsMixin:
    """新插件指标采集（business_capabilities / fuzzy_match / dom_sizer / region_tree / click_fallback / dom_diff）。

    采集原则: 仅在有明确结果时记录，try/except 保护，不影响主流程。
    """

    def record_capability_hit(
        self,
        project: str,
        run_id: str,
        capability_hint: str,
        mode: str = "text",
        score: float = 0.0,
        case_preview: str = "",
    ):
        """记录一次业务能力命中（search_or_filter / create_record / login 等 12 种）。

        mode: "text"（纯文本匹配，无 DOM 证据）或 "dom"（DOM 证据，可信度更高）
        """
        with guard("record_capability_hit: details = { 'capability': capability", log=_log):
            details = {
                "capability": capability_hint,
                "mode": mode,
                "intent": case_preview[:160],
            }
            safe_hint = (capability_hint or "unknown")[:80]
            self.record(
                project,
                run_id,
                step=4,
                metric_type="capability",
                metric_name="cap_hit",
                category=safe_hint,
                value=float(score),
                total=1.0,
                details=details,
            )
            # 同时按能力类型分别记一条，便于查询分布
            self.record(
                project,
                run_id,
                step=4,
                metric_type="capability",
                metric_name=f"hit_{safe_hint}",
                category=mode,
                value=1,
                total=1,
                details=details,
            )
            # 总数计数（便于计算能力命中率 = 总命中 / 总调用）
            self.record(
                project,
                run_id,
                step=4,
                metric_type="capability",
                metric_name="cap_total_calls",
                category="total",
                value=1,
                total=1,
            )

    def record_capability_miss(self, project: str, run_id: str, reason: str = ""):
        """记录一次业务能力未命中（用于计算能力命中率）。

        ★ 根因修复(2026-09-20)：
        原实现只写 cap_miss，未把 miss 计入 cap_total_calls，导致
        cap_total_calls 仅累计命中次数 → 门禁出现 cap_miss(194) > cap_total_calls(3)
        的矛盾，命中率分母缺了全部 miss。现 miss 与 hit 对称地各计一次
        cap_total_calls，保证 总调用 = 命中 + 未命中，命中率口径自洽。
        """
        with guard("record_capability_miss: self.record( project, run_id, step=4", log=_log):
            self.record(
                project,
                run_id,
                step=4,
                metric_type="capability",
                metric_name="cap_miss",
                category="miss",
                value=1,
                total=1,
                details={"reason": reason[:160]},
            )
            # 未命中也是一次能力调用，应计入总调用（与 record_capability_hit 对称）
            self.record(
                project,
                run_id,
                step=4,
                metric_type="capability",
                metric_name="cap_total_calls",
                category="total",
                value=1,
                total=1,
            )

    def record_fuzzy_match(
        self,
        project: str,
        run_id: str,
        candidates_fetched: int,
        concept_score: float = 0.0,
        char_score: float = 0.0,
        source_intent: str = "",
    ):
        """记录一次模糊匹配召回结果（精确匹配失败后的语义兜底）。

        candidates_fetched: 召回的候选数（>0 才记录为有召回）
        concept_score / char_score: semantic_similarity 的两路分
        """
        with guard("record_fuzzy_match: details = { 'source_intent': source_", log=_log):
            details = {
                "source_intent": source_intent[:160],
                "char_score": float(char_score),
            }
            self.record(
                project,
                run_id,
                step=4,
                metric_type="fuzzy_match",
                metric_name="fuzzy_recall_total",
                category="total",
                value=1,
                total=1,
            )
            if candidates_fetched > 0:
                self.record(
                    project,
                    run_id,
                    step=4,
                    metric_type="fuzzy_match",
                    metric_name="fuzzy_recall_hit",
                    category="hit",
                    value=float(candidates_fetched),
                    total=1,
                    details=details,
                )
                self.record(
                    project,
                    run_id,
                    step=4,
                    metric_type="fuzzy_match",
                    metric_name="fuzzy_concept_score",
                    category="score",
                    value=float(concept_score),
                    total=1,
                    details=details,
                )
                self.record(
                    project,
                    run_id,
                    step=4,
                    metric_type="fuzzy_match",
                    metric_name="fuzzy_char_score",
                    category="score",
                    value=float(char_score),
                    total=1,
                    details=details,
                )
            else:
                self.record(
                    project,
                    run_id,
                    step=4,
                    metric_type="fuzzy_match",
                    metric_name="fuzzy_recall_miss",
                    category="miss",
                    value=1,
                    total=1,
                    details=details,
                )

    def record_dom_sizer(
        self,
        project: str,
        run_id: str,
        limit: int,
        intent_keyword: str = "",
        context_mode: str = "",
    ):
        """记录一次动态 DOM 上限决策。用于观察：登录场景是否真的 compact（省 token）。"""
        with guard("record_dom_sizer: details = { 'limit': int(limit), 'ke", log=_log):
            details = {
                "limit": int(limit),
                "keyword": (intent_keyword or "")[:60],
                "mode": context_mode,
            }
            self.record(
                project,
                run_id,
                step=4,
                metric_type="dom_sizer",
                metric_name=f"limit_{limit}",
                category="count",
                value=1,
                total=1,
                details=details,
            )
            # 额外记录一条聚合用：limit_value，供 Dashboard 计算平均值
            self.record(
                project,
                run_id,
                step=4,
                metric_type="dom_sizer",
                metric_name="limit_value",
                category=context_mode or "normal",
                value=float(limit),
                total=150.0,
                details=details,
            )  # total=150 表示上限最大

    def record_region_tree(
        self,
        project: str,
        run_id: str,
        total_regions: int,
        total_elements: int,
        dominant_region_name: str = "",
    ):
        """记录一次 DOM 语义区域树构建结果。观察：复杂页面能分成多少区域。"""
        with guard("record_region_tree: details = { 'regions': int(total_reg", log=_log):
            details = {
                "regions": int(total_regions),
                "elements": int(total_elements),
                "dominant": (dominant_region_name or "")[:60],
            }
            self.record(
                project,
                run_id,
                step=4,
                metric_type="region_tree",
                metric_name="regions_count",
                category="summary",
                value=float(total_regions),
                total=float(max(total_regions, 1)),
                details=details,
            )
            self.record(
                project,
                run_id,
                step=4,
                metric_type="region_tree",
                metric_name="elements_count",
                category="summary",
                value=float(total_elements),
                total=float(max(total_elements, 1)),
                details=details,
            )

    def record_click_fallback_stats(self, project: str, run_id: str, stats: dict):
        """★ KEEP-07: 记录五级点击回退命中率。"""
        if not stats:
            return
        for key, val in stats.items():
            if key == "total":
                continue
            self.record(
                project,
                run_id,
                step=4,
                metric_type="click_fallback",
                metric_name=key,
                category="click_fallback",
                value=float(val),
                total=stats.get("total", 1),
            )

    def record_dom_diff(
        self,
        project: str,
        run_id: str,
        action_type: str = "",
        changed: bool = False,
        before: dict = None,
        after: dict = None,
    ):
        """★ KEEP-06: 记录动作分发前后的 DOM 差异摘要。"""
        before = before or {}
        after = after or {}
        text_delta = int(after.get("text_len", 0) or 0) - int(
            before.get("text_len", 0) or 0
        )
        elem_delta = int(after.get("element_count", 0) or 0) - int(
            before.get("element_count", 0) or 0
        )
        details = {
            "before_url": before.get("url", ""),
            "after_url": after.get("url", ""),
            "before_title": before.get("title", ""),
            "after_title": after.get("title", ""),
            "text_delta": text_delta,
            "element_delta": elem_delta,
        }
        self.record(
            project,
            run_id,
            step=4,
            metric_type="dom_diff",
            metric_name=action_type or "unknown",
            category="changed" if changed else "unchanged",
            value=1 if changed else 0,
            total=1,
            details=details,
        )
