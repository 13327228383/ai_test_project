# -*- coding: utf-8 -*-
"""Rerank mixin。"""

import logging

_log = logging.getLogger(__name__)


class SearchRerankMixin:
    """可选 cross-encoder rerank 相关方法。"""

    def _maybe_rerank(
        self, query: str, candidates: list[dict], top_k: int
    ) -> list[dict]:
        """可选 cross-encoder rerank。失败安静 fallback 原顺序。"""
        try:
            from src.core.pipeline_config import pipeline_config

            enabled = bool(getattr(pipeline_config, "rag_enable_rerank", False))
        except Exception as _ce:
            _log.debug("[rerank] 配置读取失败(用False): %s", str(_ce)[:50])
            enabled = False
        if not enabled or not candidates:
            return candidates[:top_k]
        try:
            from sentence_transformers import CrossEncoder  # type: ignore
        except Exception as e:
            _log.warning("CrossEncoder加载异常: %s", e, exc_info=True)
            return candidates[:top_k]
        try:
            model = CrossEncoder("BAAI/bge-reranker-base")
            pairs = [(query, str(c.get("text", ""))[:512]) for c in candidates]
            scores = model.predict(pairs)
            for c, s in zip(candidates, scores):
                try:
                    c["_rerank"] = float(s)
                except Exception as _fe:
                    _log.debug("[rerank] score转换失败(用0.0): %s", str(_fe)[:50])
                    c["_rerank"] = 0.0
            ordered = sorted(
                candidates, key=lambda x: x.get("_rerank", 0.0), reverse=True
            )
            return ordered[:top_k]
        except Exception as _re:
            _log.debug("[rerank] rerank失败(用原序): %s", str(_re)[:50])
            return candidates[:top_k]
