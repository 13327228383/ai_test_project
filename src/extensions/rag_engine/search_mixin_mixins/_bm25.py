# -*- coding: utf-8 -*-
"""BM25 搜索 mixin。"""

import math


class SearchBm25Mixin:
    """BM25 关键词搜索（回退方案）相关方法。"""

    def _build_bm25_index(self) -> None:
        """BM25 索引构建占位符（实际搜索时按需计算，无需预建）。"""
        pass

    def _bm25_search(self, query: str, chunk_type: str, top_k: int) -> list[dict]:
        """BM25 关键词搜索（回退方案）。"""
        query_keywords = self._extract_keywords(query)
        if not query_keywords:
            return []

        candidates = [c for c in self._chunks if c["type"] == chunk_type]
        if not candidates:
            return []

        # BM25 参数
        k1 = getattr(self, "_BM25_K1", 1.5)
        b = getattr(self, "_BM25_B", 0.75)
        avgdl = sum(len(c.get("keywords", [])) for c in candidates) / max(
            len(candidates), 1
        )
        N = len(candidates)

        scored = []
        for c in candidates:
            doc_kw = c.get("keywords", [])
            doc_len = len(doc_kw)
            if doc_len == 0:
                continue

            score = 0.0
            for kw in query_keywords:
                # 只在文档关键词中做精确匹配
                tf = doc_kw.count(kw)
                if tf == 0:
                    continue
                # IDF 简化计算
                df = sum(1 for cc in candidates if kw in cc.get("keywords", []))
                idf = math.log((N - df + 0.5) / (df + 0.5) + 1.0)
                # BM25 公式
                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * doc_len / max(avgdl, 1))
                score += idf * numerator / denominator

            if score > 0:
                scored.append((score, c))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            {
                "text": c["text"],
                "meta": c["meta"],
                "chunk_id": c["chunk_id"],
                "score": round(s, 4),
            }
            for s, c in scored[:top_k]
        ]
