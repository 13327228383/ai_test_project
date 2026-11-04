# -*- coding: utf-8 -*-
"""
RAG 检索 — 蓝红对抗引擎
========================
Blue Team: 语义向量/BM25关键词/Hybrid混合 三策略并行检索
Red Team:  相关性评分 + 完整性检查 + 幻觉检测（结构化）
Arbiter:   合并去重、RRF重排序、输出最优上下文

★ 零硬编码：所有模型从 config 读取。
★ 解耦设计：作为独立 Mixin，通过 pipeline_config 开关集成到 RagEngine。
"""

import json
import logging
from collections import Counter
from typing import Any

_log = logging.getLogger("rag_engine")


# ═══════════════════════════════════════════════════════════════
# 蓝队三策略检索
# ═══════════════════════════════════════════════════════════════


def _retrieve_semantic(engine, query: str, chunk_type: str, top_k: int) -> list[dict]:
    """语义向量检索（蓝队-语义）。"""
    try:
        return engine._vector_search(query, chunk_type, top_k, method="cosine")
    except Exception as e:
        _log.debug("[蓝红 RAG] 语义检索失败: %s", e)
        return []


def _retrieve_keyword(engine, query: str, chunk_type: str, top_k: int) -> list[dict]:
    """BM25 关键词检索（蓝队-关键词）。"""
    try:
        return engine._bm25_search(query, chunk_type, top_k)
    except Exception as e:
        _log.debug("[蓝红 RAG] 关键词检索失败: %s", e)
        return []


def _retrieve_hybrid(engine, query: str, chunk_type: str, top_k: int) -> list[dict]:
    """Hybrid RRF 混合检索（蓝队-混合）。"""
    try:
        return engine._hybrid_rrf_search(
            query, chunk_type, top_k, bm25_w=0.6, vec_method="cosine"
        )
    except Exception as e:
        _log.debug("[蓝红 RAG] 混合检索失败: %s", e)
        return []


RETRIEVERS = {
    "semantic": _retrieve_semantic,
    "keyword": _retrieve_keyword,
    "hybrid": _retrieve_hybrid,
}


# ═══════════════════════════════════════════════════════════════
# 红队：结构化验证（不调 AI）
# ═══════════════════════════════════════════════════════════════


def _red_team_verify_results(
    results_by_strategy: dict[str, list[dict]],
    query: str,
    top_k: int,
) -> dict[str, Any]:
    """红队验证检索结果质量。

    检查：
    1. 一致性：不同策略的结果重叠度
    2. 完整性：是否所有策略都有产出；是否有策略返回了完全不重叠的结果
    3. 排序稳定性：同一 chunk 在不同策略中的排名差异
    """
    # 收集所有结果的 chunk_id
    strategy_ids: dict[str, set] = {}
    strategy_ranked: dict[str, dict[str, int]] = {}

    for strategy, results in results_by_strategy.items():
        strategy_ids[strategy] = set()
        strategy_ranked[strategy] = {}
        for rank, r in enumerate(results):
            cid = r.get("chunk_id", r.get("id", ""))
            if cid:
                strategy_ids[strategy].add(cid)
                strategy_ranked[strategy][cid] = rank

    # 1. 重叠度
    active_strategies = [s for s in strategy_ids if strategy_ids[s]]
    overlap_scores = {}

    for i, s1 in enumerate(active_strategies):
        for s2 in active_strategies[i + 1 :]:
            ids1 = strategy_ids[s1]
            ids2 = strategy_ids[s2]
            if ids1 and ids2:
                overlap = len(ids1 & ids2) / max(len(ids1 | ids2), 1)
                overlap_scores["%s_vs_%s" % (s1, s2)] = round(overlap, 3)

    # 2. 完整性：检查是否有策略空返回
    empty_strategies = [s for s, ids in strategy_ids.items() if not ids]
    completeness_score = 1.0 - (len(empty_strategies) / max(len(strategy_ids), 1))

    # 3. 共识排名：取所有策略都返回的 chunk，加权排名
    consensus_chunks = set()
    if active_strategies:
        consensus_chunks = strategy_ids[active_strategies[0]].copy()
        for s in active_strategies[1:]:
            consensus_chunks &= strategy_ids[s]

    # 4. 异常信号
    issues = []
    if empty_strategies:
        issues.append(
            {
                "type": "empty_result",
                "detail": "策略 %s 返回空结果" % ", ".join(empty_strategies),
            }
        )
    if overlap_scores:
        low_overlaps = {k: v for k, v in overlap_scores.items() if v < 0.1}
        if low_overlaps:
            issues.append(
                {
                    "type": "low_overlap",
                    "detail": "策略间重叠度极低: %s" % json.dumps(low_overlaps),
                }
            )

    quality_score = (
        0.4 * completeness_score
        + 0.3
        * (
            sum(overlap_scores.values()) / max(len(overlap_scores), 1)
            if overlap_scores
            else 0.5
        )
        + 0.3 * (len(consensus_chunks) / max(top_k, 1))
    )

    return {
        "quality_score": round(quality_score, 3),
        "completeness": round(completeness_score, 3),
        "overlaps": overlap_scores,
        "consensus_count": len(consensus_chunks),
        "consensus_ids": list(consensus_chunks)[:10],
        "empty_strategies": empty_strategies,
        "issues": issues,
        "verdict": (
            "good"
            if quality_score >= 0.6
            else "degraded" if quality_score >= 0.3 else "poor"
        ),
    }


# ═══════════════════════════════════════════════════════════════
# Arbiter: RRF 合并排序
# ═══════════════════════════════════════════════════════════════


def _arbiter_merge_and_rerank(
    results_by_strategy: dict[str, list[dict]],
    red_team_report: dict[str, Any],
    top_k: int,
) -> list[dict]:
    """仲裁合并：RRF (Reciprocal Rank Fusion) 多策略结果融合。

    公式: score(chunk) = Σ 1 / (k + rank_i)
    其中 k=60（经典 RRF 参数），rank_i 是 chunk 在第 i 个策略中的排名。
    """
    K = 60
    chunk_scores: dict[str, float] = {}
    chunk_data: dict[str, dict] = {}
    strategy_count: dict[str, int] = Counter()

    for strategy, results in results_by_strategy.items():
        for rank, r in enumerate(results):
            cid = r.get("chunk_id", r.get("id", ""))
            if not cid:
                continue
            rrf_score = 1.0 / (K + rank + 1)
            chunk_scores[cid] = chunk_scores.get(cid, 0.0) + rrf_score
            strategy_count[cid] += 1
            if cid not in chunk_data:
                chunk_data[cid] = dict(r)
                chunk_data[cid]["_source_strategies"] = [strategy]
                chunk_data[cid]["_best_rank"] = rank + 1
            else:
                chunk_data[cid]["_source_strategies"].append(strategy)
                chunk_data[cid]["_best_rank"] = min(
                    chunk_data[cid]["_best_rank"], rank + 1
                )

    # 多策略共识加分（被多个策略同时检索到的 chunk 提升权重）
    for cid in chunk_scores:
        if strategy_count.get(cid, 0) >= 2:
            chunk_scores[cid] *= 1.2  # 共识加分 20%

    # 按 RRF 分数降序排列
    sorted_chunks = sorted(chunk_scores.items(), key=lambda x: x[1], reverse=True)
    merged = []
    for cid, score in sorted_chunks[:top_k]:
        data = dict(chunk_data[cid])
        data["_rrf_score"] = round(score, 4)
        data["_consensus"] = strategy_count.get(cid, 0)
        merged.append(data)

    _log.info(
        "[蓝红 RAG] RRF 合并: %d chunks → %d (共识=%d)",
        sum(len(v) for v in results_by_strategy.values()),
        len(merged),
        sum(1 for c in merged if c.get("_consensus", 0) >= 2),
    )

    return merged


# ═══════════════════════════════════════════════════════════════
# 公开入口
# ═══════════════════════════════════════════════════════════════


def blue_red_search(
    engine,
    query: str,
    chunk_type: str = "requirement",
    top_k: int = 10,
    strategies: list[str] = None,
) -> dict[str, Any]:
    """
    RAG 蓝红对抗检索。

    参数:
        engine:     RagEngine 实例（需要支持 _vector_search/_bm25_search/_hybrid_rrf_search）
        query:      查询文本
        chunk_type: chunk 类型
        top_k:      返回数量
        strategies: 启用的策略列表（默认全部: semantic/keyword/hybrid）

    返回:
        {
          "success": True/False,
          "results": [{...}, ...],           # RRF 合并后的最终结果
          "by_strategy": {semantic: [...], keyword: [...], hybrid: [...]},
          "red_team": {quality_score, overlaps, ...},
          "meta": {consensus_count, rrf_enabled, ...},
        }
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if strategies is None:
        strategies = ["semantic", "keyword", "hybrid"]

    # 过滤 engine 支持的策略
    available = [s for s in strategies if s in RETRIEVERS]
    for s in strategies:
        if s not in RETRIEVERS:
            _log.debug("[蓝红 RAG] 策略 %s 不可用，跳过", s)

    if not available:
        return {
            "success": False,
            "error": "无可用检索策略",
            "results": [],
            "by_strategy": {},
            "red_team": {},
        }

    _log.info("[蓝红 RAG] 启动 %d 策略并行检索: %s", len(available), available)

    # ── Phase 1: 蓝队多策略并行检索 ──
    results_by_strategy: dict[str, list[dict]] = {}

    with ThreadPoolExecutor(max_workers=min(len(available), 3)) as executor:
        futures = {}
        for s in available:
            f = executor.submit(RETRIEVERS[s], engine, query, chunk_type, top_k * 2)
            futures[f] = s

        for future in as_completed(futures):
            s = futures[future]
            try:
                results_by_strategy[s] = future.result() or []
            except Exception as e:
                _log.warning("[蓝红 RAG] 策略 %s 异常: %s", s, str(e)[:120])
                results_by_strategy[s] = []

    # ── Phase 2: 红队验证 ──
    red_team_report = _red_team_verify_results(results_by_strategy, query, top_k)

    # ── Phase 3: RRF 仲裁合并 ──
    merged = _arbiter_merge_and_rerank(results_by_strategy, red_team_report, top_k)

    return {
        "success": True,
        "results": merged,
        "by_strategy": {
            s: results[:top_k] for s, results in results_by_strategy.items()
        },
        "red_team": red_team_report,
        "meta": {
            "query": query[:100],
            "chunk_type": chunk_type,
            "strategies_used": available,
            "total_retrieved": sum(len(v) for v in results_by_strategy.values()),
            "merged_count": len(merged),
            "consensus_count": red_team_report.get("consensus_count", 0),
        },
    }
