# -*- coding: utf-8 -*-
"""关键词提取 mixin"""

import re
import logging
from collections import Counter

_log = logging.getLogger("rag_engine")


class ChunkingKeywordsMixin:
    """关键词提取相关方法。"""

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        if not text:
            return []

        keywords = []
        english_words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text)
        keywords.extend(w.lower() for w in english_words)

        paths = re.findall(r"/[a-zA-Z0-9/_\-]+", text)
        for p in paths:
            keywords.append(p.lower())

        chinese_text = "".join(re.findall(r"[一-鿿]+", text))
        if chinese_text:
            cut_words = ChunkingKeywordsMixin._tokenize_chinese(chinese_text)
            keywords.extend(w for w in cut_words if len(w) >= 2)

        counter = Counter(keywords)
        result = [
            k
            for k, v in counter.items()
            if len(k) >= 3 or v >= 2 or re.match(r"^[一-鿿]{2,}$", k)
        ]
        # 使用类属性或默认值
        max_count = getattr(ChunkingKeywordsMixin, "_KEYWORD_MAX_COUNT", 50)
        return result[:max_count]

    @staticmethod
    def _tokenize_chinese(text: str) -> list[str]:
        try:
            import jieba

            return [w for w in jieba.cut(text) if len(w.strip()) >= 1]
        except ImportError:
            words = []
            for i in range(len(text) - 1):
                words.append(text[i : i + 2])
            for i in range(len(text) - 2):
                words.append(text[i : i + 3])
            return words
