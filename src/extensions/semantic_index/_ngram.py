# -*- coding: utf-8 -*-
"""字符 n-gram 轻量级向量（捕获部分语义，比 hash 更精准）。"""
import hashlib

from ._engine import _normalize

# ★ P2-1: 字符 n-gram 轻量级向量（捕获部分语义，比 hash 更精准）
_NGRAM_SIZES = (2, 3, 4)  # 中文 bi-gram / tri-gram / quad-gram
_NGRAM_VEC_DIM = 384


def _ngram_fallback(text: str) -> list[float]:
    """
    基于字符 n-gram 的轻量级语义向量。
    不依赖任何外部库，对中文有较好的局部语义捕捉能力。
    """
    vec = [0.0] * _NGRAM_VEC_DIM
    text = str(text).strip().lower()
    if not text:
        return _normalize(vec)

    ngram_count = 0
    for n in _NGRAM_SIZES:
        for i in range(len(text) - n + 1):
            ngram = text[i : i + n]
            # 用hash映射到向量维度
            idx = (
                int(hashlib.md5(ngram.encode("utf-8")).hexdigest(), 16) % _NGRAM_VEC_DIM
            )
            vec[idx] += 1.0
            ngram_count += 1

    # 词级增强：合并原有词级hash（对英文单词友好）
    for w in text.split():
        if len(w) >= 2:
            idx = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16) % _NGRAM_VEC_DIM
            vec[idx] += 0.5  # 词级权重减半

    return _normalize(vec)
