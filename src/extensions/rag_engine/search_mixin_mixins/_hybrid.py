# -*- coding: utf-8 -*-
"""混合搜索 (RRF) mixin。"""

import math
import logging

_log = logging.getLogger("rag_engine")


class SearchHybridMixin:
    """混合 RRF 搜索：融合向量相似度与 BM25 关键词。"""

    def _hybrid_rrf_search(
        self,
        query: str,
        chunk_type: str,
        top_k: int,
        bm25_weight: float = 0.7,
        vector_method: str = "dot",
    ) -> list[dict]:
        """混合 RRF：融合向量相似度与 BM25 关键词。"""
        query_vec = self._embed_text(query) if self._use_embeddings else None
        candidates = [c for c in self._chunks if c["type"] == chunk_type]
        if not candidates:
            return []
        # --- 向量评分 ---
        vector_scores = {}
        if query_vec:
            for c in candidates:
                v = c.get("_vector")
                if not v:
                    continue
                if vector_method == "dot":
                    score = self._dot_product(query_vec, v)
                else:
                    score = self._cosine_sim(query_vec, v)
                vector_scores[c["chunk_id"]] = score
        # --- BM25 评分 ---
        bm25_scores = {}
        query_keywords = self._extract_keywords(query)
        if query_keywords:
            k1 = getattr(self, "_BM25_K1", 1.5)
            b = getattr(self, "_BM25_B", 0.75)
            avgdl = sum(len(c.get("keywords", [])) for c in candidates) / max(
                len(candidates), 1
            )
            N = len(candidates)
            for c in candidates:
                doc_kw = c.get("keywords", [])
                doc_len = len(doc_kw)
                if doc_len == 0:
                    continue
                score = 0.0
                for kw in query_keywords:
                    tf = doc_kw.count(kw)
                    if tf == 0:
                        continue
                    df = sum(1 for cc in candidates if kw in cc.get("keywords", []))
                    idf = math.log((N - df + 0.5) / (df + 0.5) + 1.0)
                    score += (
                        idf
                        * tf
                        * (k1 + 1)
                        / (tf + k1 * (1 - b + b * doc_len / max(avgdl, 1)))
                    )
                if score > 0:
                    bm25_scores[c["chunk_id"]] = score
        # --- RRF 融合 ---
        try:
            from src.core.config_loader import config as _cfg

            rrf_k = int(getattr(_cfg, "rag_rrf_k", 60))
        except Exception as _ce:
            _log.debug("[hybrid] rrf_k配置读取失败(用60): %s", str(_ce)[:50])
            rrf_k = 60
        rrf_scores = {}
        if vector_scores:
            vec_sorted = sorted(vector_scores.items(), key=lambda x: x[1], reverse=True)
            vec_w = 1.0 - bm25_weight
            for rank, (cid, _) in enumerate(vec_sorted):
                rrf_scores[cid] = rrf_scores.get(cid, 0) + vec_w / (rrf_k + rank + 1)
        if bm25_scores:
            bm_sorted = sorted(bm25_scores.items(), key=lambda x: x[1], reverse=True)
            for rank, (cid, _) in enumerate(bm_sorted):
                rrf_scores[cid] = rrf_scores.get(cid, 0) + bm25_weight / (
                    rrf_k + rank + 1
                )
        if not rrf_scores:
            return self._bm25_search(query, chunk_type, top_k)
        final_sorted = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        chunk_by_id = {c["chunk_id"]: c for c in candidates}
        try:
            from src.core.config_loader import config as _cfg

            threshold = float(getattr(_cfg, "rag_vector_search_threshold", 0.3))
        except Exception as _ce:
            _log.debug("[hybrid] threshold配置读取失败(用0.3): %s", str(_ce)[:50])
            threshold = 0.3
        result = []
        for cid, score in final_sorted[:top_k]:
            c = chunk_by_id.get(cid)
            if not c:
                continue
            vs = vector_scores.get(cid, 0)
            bs = bm25_scores.get(cid, 0)
            if (query_vec and vs < threshold) and bs == 0:
                continue
            result.append(
                {
                    "text": c["text"],
                    "meta": c.get("meta", ""),
                    "chunk_id": c["chunk_id"],
                    "score": round(score, 4),
                }
            )
        return result

    def _detect_best_metric(self) -> tuple:
        """
        ★ 自动推断最佳相似度方法：
        步骤 1：抽样检查向量 L2 范数 → 若 |v|₂ ≈ 1 则用点积替代余弦
        步骤 2：抽样检查 BM25 与向量的 top-3 重叠率 → 若重叠 < 0.5 则启用混合 RRF
        返回 (metric_name, bm25_weight_or_None, vector_method_or_None)
        """
        # --- 读取配置 ---
        try:
            from src.core.config_loader import config as _cfg

            metric_cfg = str(getattr(_cfg, "rag_similarity_metric", "auto")).lower()
            weight_cfg = getattr(_cfg, "rag_hybrid_bm25_weight", None)
        except Exception as _ce:
            _log.debug("[hybrid] similarity配置读取失败(用默认): %s", str(_ce)[:50])
            metric_cfg, weight_cfg = "auto", None
        # 非 auto：直接使用配置
        if metric_cfg != "auto":
            if metric_cfg == "hybrid":
                w = (
                    weight_cfg
                    if isinstance(weight_cfg, (int, float)) and 0 <= weight_cfg <= 1
                    else 0.7
                )
                return ("hybrid", w, "dot")
            return (metric_cfg, None)
        # --- 步骤 A：检测向量归一化 ---
        all_vecs = [c.get("_vector") for c in self._chunks if c.get("_vector")]
        if not all_vecs:
            return ("bm25", None)
        sample_size = min(10, len(all_vecs))
        avg_norm = sum(self._l2_norm(v) for v in all_vecs[:sample_size]) / sample_size
        is_normalized = 0.95 <= avg_norm <= 1.05
        vec_method = "dot" if is_normalized else "cosine"
        _log.info(
            "[RagEngine] auto 推断：向量平均 L2 范数=%.4f → %s", avg_norm, vec_method
        )
        # --- 步骤 B：检测互补性 ---
        test_queries = [c["text"][:200] for c in self._chunks[:5] if c.get("text")]
        total_overlap = 0.0
        valid = 0
        for q in test_queries:
            q_vec = self._embed_text(q) if self._use_embeddings else None
            if not q_vec:
                continue
            s_list = []
            for c in self._chunks:
                v = c.get("_vector")
                if not v:
                    continue
                s = (
                    self._dot_product(q_vec, v)
                    if is_normalized
                    else self._cosine_sim(q_vec, v)
                )
                s_list.append((s, c["chunk_id"]))
            s_list.sort(key=lambda x: x[0], reverse=True)
            vec_top = set(cid for _, cid in s_list[:3])
            bm25_results = self._bm25_search(q, self._chunks[0].get("type", "api"), 3)
            bm25_top = set(r.get("chunk_id", "") for r in bm25_results)
            if vec_top and bm25_top:
                total_overlap += len(vec_top & bm25_top) / max(
                    len(vec_top), len(bm25_top)
                )
                valid += 1
        avg_overlap = total_overlap / valid if valid > 0 else 0.5
        _log.info("[RagEngine] auto 推断：向量-BM25 平均重叠率=%.2f", avg_overlap)
        # --- 步骤 C：决定是否混合 ---
        if avg_overlap < 0.5:
            bm25_w = round(1.0 - avg_overlap, 2)
            _log.info(
                "[RagEngine] auto 结论：hybrid RRF（bm25_weight=%.2f）, 向量=%s",
                bm25_w,
                vec_method,
            )
            return ("hybrid", bm25_w, vec_method)
        _log.info("[RagEngine] auto 结论：%s（结果趋同，不混合）", vec_method)
        return (vec_method, None)
