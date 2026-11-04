# -*- coding: utf-8 -*-
from __future__ import annotations

"""SmartLocator —— 兜底路径的"意图 → 元素"匹配器。

定位
----
本模块是 single.py "AI 多轮循环兜底分支"的优化器：
- 在把 DOM 喂给 AI 决策之前，先用关键词匹配 + 同义词字典筛出最可能的元素
- 高置信时直接定位（0 token），中置信时 Top-K 给 AI 二选一（~200 token），
  低置信时再走全 DOM 兜底（~1500 token，保留现有兜底逻辑）

不依赖任何 AI / embedding / RAG —— 纯规则+字典，零 token 成本，纯本地计算。

接入示例
--------
    from src.pipeline.steps.step4.browser_core._smart_locator import (
        match_step_to_element, MatchResult,
    )

    result = match_step_to_element(
        step_desc="点击保存按钮",
        step_expected="弹出保存成功提示",
        case_meta={"title": "提交订单"},
        elements=dom_elements,  # 来自 engine.get_structured_state()
    )

    if result.confidence == "high":
        # 直接用 result.best_element 定位执行，跳过 AI
        ...
    elif result.confidence == "medium":
        # 把 result.candidates (≤3 个) 给 AI 二选一
        ...
    else:
        # 走全 DOM 兜底
        ...
"""

from dataclasses import dataclass, field
from typing import Any

from ._keyword_match import extract_keywords, expand_with_synonyms, score_all
from ._fusion import classify_confidence, select_top_k


@dataclass
class MatchResult:
    """匹配结果。

    字段:
        confidence:    high / medium / low
        best_element:  最佳候选（high 时直接用；medium 时是 Top1；low 时为 None）
        candidates:    Top-K 候选列表（high 也包含 best_element；medium 是 Top-K；low 为空）
                       每项: {"element": dict, "score": int, "hits": list[str]}
        keywords:      提取的关键词集合（debug 用）
        synonyms:      扩展的同义词集合（debug 用）
        debug_summary: 一行人类可读摘要（用于日志）
    """

    confidence: str
    best_element: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    keywords: set = field(default_factory=set)
    synonyms: set = field(default_factory=set)
    debug_summary: str = ""


def match_step_to_element(
    *,
    step_desc: str = "",
    step_expected: str = "",
    case_meta: dict[str, Any] | None = None,
    elements: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
) -> MatchResult:
    """主入口：基于步骤意图在剪枝 DOM 上做关键词匹配。

    参数:
        step_desc:     当前步骤描述（必传）
        step_expected: 期望结果（可选，用于扩充关键词）
        case_meta:   意图元数据（可选，title 也参与关键词提取）
        elements:      8 层剪枝后的元素列表
        actions:       结构化动作列表（可选，target_hint 参与关键词提取）

    返回:
        MatchResult
    """
    if not elements:
        return MatchResult(confidence="low", debug_summary="DOM 为空")

    # 1. 关键词提取（多源合并）
    case_meta = case_meta or {}
    sources = [step_desc or "", step_expected or "", case_meta.get("title") or ""]
    # ★ Fix (2026-07-10): 把 actions 中的 target_hint 也纳入关键词提取
    #   根因：step_desc 只是用例标题，SmartLocator 匹配不到具体操作目标
    #   通用修复：从 actions 提取 target_hint/value 等操作信息作为关键词来源
    if actions and isinstance(actions, list):
        for _act in actions:
            if not isinstance(_act, dict):
                continue
            for _key in ("target_hint", "selector_hint", "value", "text", "file"):
                _val = _act.get(_key, "")
                if _val and isinstance(_val, str):
                    sources.append(_val)
    keywords: set = set()
    for src in sources:
        keywords |= extract_keywords(src)

    if not keywords:
        return MatchResult(
            confidence="low",
            debug_summary="未提取到任何关键词（步骤描述过短或无中英文词汇）",
        )

    # 2. 同义词扩展
    original, synonyms = expand_with_synonyms(keywords)

    # 3. 全量评分
    scored = score_all(elements, original, synonyms, intent_keywords=original)

    # 4. 置信度分类
    confidence = classify_confidence(scored)

    # 5. 组装结果
    if confidence == "high":
        best = scored[0]
        return MatchResult(
            confidence="high",
            best_element=best["element"],
            candidates=[best],
            keywords=original,
            synonyms=synonyms,
            debug_summary=(
                f"HIGH match: score={best['score']} "
                f"top2_score={scored[1]['score'] if len(scored) > 1 else 0} "
                f"hits={','.join(best['hits'])}"
            ),
        )

    if confidence == "medium":
        top_k = select_top_k(scored)
        return MatchResult(
            confidence="medium",
            best_element=top_k[0]["element"],
            candidates=top_k,
            keywords=original,
            synonyms=synonyms,
            debug_summary=(
                f"MEDIUM match: top scores={[c['score'] for c in top_k]} "
                f"len={len(top_k)}"
            ),
        )

    # low 置信度
    return MatchResult(
        confidence="low",
        candidates=scored[:5],  # 给个上下文，但调用方应当走全 DOM 兜底
        keywords=original,
        synonyms=synonyms,
        debug_summary=(
            f"LOW match: top_score={scored[0]['score'] if scored else 0} "
            f"keywords_count={len(original)}"
        ),
    )


__all__ = ["MatchResult", "match_step_to_element"]
