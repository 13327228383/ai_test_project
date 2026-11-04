# -*- coding: utf-8 -*-
from __future__ import annotations

"""
semantic_dom_tree.py — DOM 语义区域树（facade）
================================================
将原有单体类拆分为 semantic_dom_tree_mixins/ 多继承模式，
本模块作为 facade，保持对外接口完全不变。
"""

from typing import Any

from .base import _SemanticDomBaseMixin
from .build import _SemanticDomBuildMixin
from .prompt import _SemanticDomPromptMixin
from .utils import (  # noqa: F401 — 重新导出所有模块级常量/函数
    _classify,
    _cluster_regions,
    _compact_element,
    _extract_keywords,
    _get_h,
    _get_tag,
    _get_text,
    _get_y,
    _INPUT_TAGS,
    _BUTTON_TAGS,
    _LINK_TAGS,
    _TABLE_TAGS,
    _record_tree_metric,
    _region_name_and_summary,
    _TYPE_BUTTON,
    _TYPE_INPUT,
    _TYPE_LINK,
    _TYPE_OTHER,
    _TYPE_TABLE,
)


# ---------------------------------------------------------------------------
# 对外主类 SemanticDomTree（mixin 组合）
# ---------------------------------------------------------------------------
class SemanticDomTree(
    _SemanticDomBaseMixin, _SemanticDomBuildMixin, _SemanticDomPromptMixin
):
    """DOM 语义区域树（单例）。"""

    pass


# ---------------------------------------------------------------------------
# 便捷函数（直接操作单例）
# ---------------------------------------------------------------------------
def build_region_tree(
    elements: Any, goal_text: str = "", max_elements_per_region: int = 12
) -> dict[str, Any]:
    return semantic_dom_tree.build_region_tree(
        elements, goal_text, max_elements_per_region
    )


def regions_to_prompt_text(
    region_tree: dict[str, Any], max_regions: int = 5, include_elements: bool = True
) -> str:
    return semantic_dom_tree.regions_to_prompt_text(
        region_tree, max_regions, include_elements
    )


def generate_ai_hint(goal_text: str, region_tree: dict[str, Any] | None = None) -> str:
    return semantic_dom_tree.generate_ai_hint(goal_text, region_tree)


# 单例实例
# ★ 方案C 改造：semantic_dom_tree 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

semantic_dom_tree = PerProjectRegistry(SemanticDomTree)
