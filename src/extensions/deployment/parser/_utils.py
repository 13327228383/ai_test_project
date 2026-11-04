# -*- coding: utf-8 -*-
"""
parser._utils — 静态工具方法
============================
extract_keywords_static, cosine_sim_static
"""

import re
import math


def extract_keywords_static(text: str) -> list[str]:
    """中文/英文关键词提取（不依赖 jieba）。"""
    if not text:
        return []
    keywords = []
    english_words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text)
    keywords.extend(w.lower() for w in english_words)
    chinese_chars = re.findall(r"[一-鿿]+", text)
    for segment in chinese_chars:
        for n in [2, 3]:
            for i in range(len(segment) - n + 1):
                keywords.append(segment[i : i + n])
    return keywords


def cosine_sim_static(a: list[float], b: list[float]) -> float:
    """余弦相似度。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0
