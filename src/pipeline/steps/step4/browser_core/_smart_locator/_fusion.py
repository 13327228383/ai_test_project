# -*- coding: utf-8 -*-
"""Top-K 选择 + 置信度判定。

三层路径：
- HIGH   : 直接命中——Top1 分数 ≥ HIGH_SCORE_MIN 且与 Top2 差距 ≥ HIGH_GAP_MIN
- MEDIUM : Top-K AI 二选一——Top1 分数 ≥ MEDIUM_SCORE_MIN
- LOW    : 全 DOM 兜底——其余情况（包括 Top1 分数 = 0）

阈值是经验值，跑通后按真实漏匹配/误匹配 case 调整。
"""

from typing import Any


# ─────────── 阈值配置（v0.1 经验值）───────────
HIGH_SCORE_MIN = 15  # 高置信下限：意味着"text 命中原词"(+10) + "tag 加权"(+5) 起步
HIGH_GAP_MIN = 5  # 高置信要求：Top1 和 Top2 拉开足够距离（避免歧义）
MEDIUM_SCORE_MIN = (
    8  # 中置信下限：意味着至少有一个高权字段（text/aria-label/placeholder）命中
)
MEDIUM_TOP_K = 3  # 中置信时给 AI 看的候选数


def classify_confidence(scored: list[dict[str, Any]]) -> str:
    """根据评分列表分类置信度。"""
    if not scored:
        return "low"
    top1 = scored[0]["score"]
    top2 = scored[1]["score"] if len(scored) > 1 else 0

    if top1 >= HIGH_SCORE_MIN and (top1 - top2) >= HIGH_GAP_MIN:
        return "high"
    if top1 >= MEDIUM_SCORE_MIN:
        return "medium"
    return "low"


def select_top_k(
    scored: list[dict[str, Any]], k: int = MEDIUM_TOP_K
) -> list[dict[str, Any]]:
    """取分数最高的 K 个候选。"""
    return scored[:k]


__all__ = [
    "classify_confidence",
    "select_top_k",
    "HIGH_SCORE_MIN",
    "HIGH_GAP_MIN",
    "MEDIUM_SCORE_MIN",
    "MEDIUM_TOP_K",
]
