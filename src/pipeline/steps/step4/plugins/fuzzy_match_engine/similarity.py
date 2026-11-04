# -*- coding: utf-8 -*-
"""相似度算法：Jaccard + 双通路语义相似度 — 从 fuzzy_match_engine.py 拆出，零逻辑变更。"""

import logging
from typing import Set

from .tokenizer import tokenize, _concept_tokens_only, _char_tokens_only

_log = logging.getLogger(__name__)


def jaccard(a: Set[str], b: Set[str]) -> float:
    """Jaccard 相似度: |a ∩ b| / |a ∪ b|。空集返回 0.0。"""
    try:
        if not a or not b:
            return 0.0
        inter = len(a & b)
        if inter == 0:
            return 0.0
        uni = len(a | b)
        if uni == 0:
            return 0.0
        return inter / uni
    except Exception as e:
        _log.warning("fuzzy_match_engine.jaccard 异常 %s", e)
        return 0.0


def semantic_similarity(text_a: str, text_b: str) -> float:
    """
    ★ 核心: 双通路语义相似度（推荐替代纯 jaccard 做意图匹配）。

    思路:
      - Concept 通路（语义）: 提取两个文本的 concept_* token，算 Jaccard → 代表"意图是否一致"
      - Character 通路（字面）: 提取两个文本的非 concept token（2-gram+英文词），算 Jaccard → 代表"字面是否像"
      - 加权组合: concept_score * 0.6 + char_score * 0.4（语义 > 字面）

    返回: 0.0 ~ 1.0 的浮点数，越大越相似。
    典型效果:
      - "创建订单记录" vs "新增订单" → concept_jaccard 很高（都有 concept_create + concept_record）
      - "登录系统" vs "搜索数据" → 两路都低
    """
    try:
        if not text_a or not text_b:
            return 0.0
        a_tokens = tokenize(text_a, use_semantic=True)
        b_tokens = tokenize(text_b, use_semantic=True)

        # 通路 1: 概念层（语义） — 60% 权重
        a_concept = _concept_tokens_only(a_tokens)
        b_concept = _concept_tokens_only(b_tokens)
        concept_score = jaccard(a_concept, b_concept)  # 0.0 ~ 1.0

        # 通路 2: 字符层（字面） — 40% 权重
        a_char = _char_tokens_only(a_tokens)
        b_char = _char_tokens_only(b_tokens)
        char_score = jaccard(a_char, b_char)

        # 组合: 如果概念层已经 ≥ 0.3 且有 2+ 个共同概念 → 至少给 0.25 保底
        common_concepts = len(a_concept & b_concept)
        if common_concepts >= 2 and concept_score >= 0.3:
            # 语义高度重合：至少给 0.25 保底
            combined = max(concept_score * 0.6 + char_score * 0.4, 0.25)
        else:
            combined = concept_score * 0.6 + char_score * 0.4

        # 再额外: 如果有共同的订单/客户等具体实体概念，追加 bonus
        if "concept_order" in a_concept and "concept_order" in b_concept:
            combined = min(1.0, combined + 0.05)

        return round(combined, 4)
    except Exception as e:
        _log.warning("fuzzy_match_engine.semantic_similarity 异常 %s", e)
        return 0.0
