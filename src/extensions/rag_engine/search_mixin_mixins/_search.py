# -*- coding: utf-8 -*-

"""统一搜索入口 mixin。"""


import logging

_log = logging.getLogger(__name__)


class SearchEntryMixin:
    """统一搜索入口：根据 _detected_metric 分派。"""

    def _search(self, query: str, chunk_type: str, top_k: int) -> list[dict]:
        """统一搜索入口：根据 _detected_metric 分派。



        ★ v6: 蓝红对抗多策略检索（可通过 pipeline_config 关闭）。

        """

        # ★ v6: 蓝红对抗多策略并行检索

        # 全局开关：step4_red_team_enabled（流水线配置 UI）为主控，

        # rag_blue_red_enabled（环境变量）为独立覆盖

        try:

            from src.core.pipeline_config import pipeline_config

            _global_red = getattr(pipeline_config, "step4_red_team_enabled", False)

            _rag_blue = getattr(pipeline_config, "rag_blue_red_enabled", True)

            if _global_red or _rag_blue:

                from ._blue_red import blue_red_search

                result = blue_red_search(self, query, chunk_type, top_k)

                if result.get("success") and result.get("results"):

                    return result["results"]

                # 降级：蓝红失败回退到原始单策略

                _log = __import__("logging").getLogger("rag_engine")

                _log.debug("[蓝红 RAG] 降级到单策略检索")

        except Exception as e:

            import logging

            logging.getLogger("rag_engine").debug(
                "[蓝红 RAG] 不可用，回退单策略: %s", e
            )

        # 原始单策略分派

        if not self._use_embeddings:

            return self._bm25_search(query, chunk_type, top_k)

        if self._detected_metric is None:

            self._detected_metric = self._detect_best_metric()

        metric_info = self._detected_metric

        metric = metric_info[0]

        if metric == "bm25":

            return self._bm25_search(query, chunk_type, top_k)

        if metric == "hybrid":

            bm25_w = metric_info[1] if len(metric_info) > 1 else 0.7

            vec_method = metric_info[2] if len(metric_info) > 2 else "dot"

            return self._hybrid_rrf_search(query, chunk_type, top_k, bm25_w, vec_method)

        if metric == "dot":

            return self._vector_search(query, chunk_type, top_k, method="dot")

        return self._vector_search(query, chunk_type, top_k, method="cosine")
