# -*- coding: utf-8 -*-
"""
semantic_dom_tree_mixins._build — 区域树构建 mixin（build_region_tree）
"""

import logging
from typing import Any

from .utils import (
    _cluster_regions,
    _compact_element,
    _extract_keywords,
    _get_text,
    _record_tree_metric,
    _region_name_and_summary,
)

_log = logging.getLogger(__name__)


class _SemanticDomBuildMixin:
    """提供 build_region_tree 方法。"""

    def build_region_tree(
        self, elements: Any, goal_text: str = "", max_elements_per_region: int = 12
    ) -> dict[str, Any]:
        """
        从扁平元素列表构建区域树。

        参数:
            elements: list 或 dict（有 elements/dom 字段）
            goal_text: 目标意图（用于 generate_ai_hint）
            max_elements_per_region: 每个区域在结果中保留的元素上限
        返回:
            {"total_regions": int, "total_elements": int, "regions": [...]}
        """
        try:
            # 1) 归一化成 list
            if isinstance(elements, list):
                raw_list: list[Any] = elements
            elif isinstance(elements, dict):
                raw_list = (
                    elements.get("elements")
                    or elements.get("dom")
                    or elements.get("nodes")
                    or []
                )
                if not isinstance(raw_list, list):
                    raw_list = [raw_list] if raw_list else []
            else:
                raw_list = []

            if not raw_list:
                tree = {
                    "total_regions": 0,
                    "total_elements": 0,
                    "regions": [],
                    "goal_text": goal_text or "",
                }
                self._last_tree = tree
                return tree

            # 2) 聚类
            regions_raw = _cluster_regions(raw_list)

            # 3) 构建每个区域的结构
            regions_out: list[dict[str, Any]] = []
            for idx, reg in enumerate(regions_raw):
                try:
                    texts = [_get_text(el) for el in reg]
                    kws = _extract_keywords(texts, top_n=5)
                    name, summary = _region_name_and_summary(reg, kws)
                    region_elems = [
                        _compact_element(el) for el in reg[:max_elements_per_region]
                    ]
                    regions_out.append(
                        {
                            "region_id": f"region_{idx}",
                            "name": name,
                            "element_count": len(reg),
                            "summary": summary,
                            "top_keywords": kws,
                            "elements": region_elems,
                        }
                    )
                except Exception as inner:
                    _log.warning("SemanticDomTree: 构建区域 %d 异常 %s", idx, inner)
                    continue

            # 4) 组装根对象
            tree = {
                "total_regions": len(regions_out),
                "total_elements": len(raw_list),
                "regions": regions_out,
                "goal_text": goal_text or "",
            }
            self._last_tree = tree

            # 5) 顺便生成一次 AI hint
            self._last_hint = self._generate_ai_hint_impl(goal_text, tree)
            # ★ KPI 埋点
            dominant = ""
            if regions_out:
                # 元素最多的区域当作主导区域
                biggest = max(regions_out, key=lambda r: r.get("element_count", 0))
                dominant = biggest.get("name", "")
            _record_tree_metric(tree["total_regions"], tree["total_elements"], dominant)
            _log.info(
                "SemanticDomTree: 构建完成，%d 个区域，%d 个元素",
                tree["total_regions"],
                tree["total_elements"],
            )
            return tree
        except Exception as e:
            _log.warning("SemanticDomTree.build_region_tree 异常 %s", e)
            # 失败时返回极简结构，避免上层抛异常
            fallback = {
                "total_regions": 0,
                "total_elements": 0,
                "regions": [],
                "goal_text": goal_text or "",
            }
            self._last_tree = fallback
            _record_tree_metric(0, 0, "exception:" + str(e)[:50])
            return fallback
