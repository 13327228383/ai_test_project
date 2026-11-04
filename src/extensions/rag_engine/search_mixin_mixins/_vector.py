# -*- coding: utf-8 -*-
"""向量搜索 mixin。"""

import logging

_log = logging.getLogger("rag_engine")


class SearchVectorMixin:
    """基于向量的语义搜索相关方法。"""

    def _vector_search(
        self, query: str, chunk_type: str, top_k: int, method: str = "cosine"
    ) -> list[dict]:
        """基于向量的语义搜索（method=dot 或 cosine）。全 MySQL：向量从 rag_chunks 加载进内存后做余弦/点积比较。"""
        query_vec = self._embed_text(query)
        if not query_vec:
            return self._bm25_search(query, chunk_type, top_k)

        # 内存余弦/点积比较（向量已从 MySQL rag_chunks 加载到 self._chunks）
        candidates = [c for c in self._chunks if c["type"] == chunk_type]
        if not candidates:
            return []
        scored = []
        for c in candidates:
            vec = c.get("_vector")
            if not vec:
                continue
            if method == "dot":
                score = self._dot_product(query_vec, vec)
            else:
                score = self._cosine_sim(query_vec, vec)
            scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        try:
            from src.core.config_loader import config as _cfg

            threshold = float(
                getattr(
                    _cfg,
                    "rag_vector_search_threshold",
                    getattr(self, "_VECTOR_SEARCH_THRESHOLD", 0.3),
                )
            )
        except Exception as _ce:
            _log.debug("[vector] threshold配置失败(用默认): %s", str(_ce)[:50])
            threshold = getattr(self, "_VECTOR_SEARCH_THRESHOLD", 0.3)
        return [
            {
                "text": c["text"],
                "meta": c.get("meta", ""),
                "chunk_id": c["chunk_id"],
                "score": round(s, 4),
            }
            for s, c in scored[:top_k]
            if s > threshold
        ]
