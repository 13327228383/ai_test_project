# -*- coding: utf-8 -*-
"""测试用例语义/关键词去重 + 质量评分工具 (v3.1) — 从 checks[] 读取。

v3.1 改进：
- Embedding 向量缓存（进程级），同文本不重复计算
- 新增 dedup_remodel() 专为重新建模场景设计（返回去重结果+重复详情）
"""

import json
from src.core.guarded import guard
import logging

_log = logging.getLogger(__name__)

# ★ v3.1: 进程级 Embedding 缓存，避免同文本重复调用 embed
_EMBED_CACHE: dict = {}
_EMBED_CACHE_MAX = 500


def _parse_checks(case: dict) -> list:
    checks = case.get("checks") or []
    if isinstance(checks, str):
        try:
            checks = json.loads(checks)
        except Exception as _e:
            _log.debug("[dedup] _parse_checks 异常: %s", _e, exc_info=True)
            checks = []
    return checks if isinstance(checks, list) else []


def _same_req_scope(nc: dict, ec: dict) -> bool:
    """★ v29 根因修复(2026-07-22): 去重比较的需求范围约束。

    根因(3层追问):
      1. 表层: REQ-008 重新生成的 3 条用例被去重标记并丢弃，需求零用例
      2. 中层: 去重对 title+checks 做跨需求 embedding 相似度比较，
              REQ-008 与 REQ-028 同为 upload_rule 生成、结构相似 → 误判重复
      3. 根因: 去重语义是"避免同一验证目标的重复覆盖"，
              验证目标=需求；跨需求的用例各自验证自己的需求，
              物理上不可能是重复（REQ-028 的用例不能作为 REQ-008 的覆盖证据）
    通用性: req_id 是所有用例的通用需求标识，跨系统/行业适用
    覆盖性: 应用于 embedding 主路径与 keywords 降级路径
    边界: 任一方 req_id 缺失 → 保守允许比较（无法判定归属时维持原行为）
    """
    n_rid = str(nc.get("req_id", "") or "").strip()
    e_rid = str(ec.get("req_id", "") or "").strip()
    if not n_rid or not e_rid:
        return True
    return n_rid == e_rid


def deduplicate_intents(
    new_intents: list,
    existing_intents: list,
    threshold: float = None,
    return_tracking: bool = False,
):
    """
    基于 Embedding 语义相似度的测试用例去重。
    相似度 > threshold 的用例视为重复，保留质量更高的。

    ★ P1: 阈值走 pipeline_config (env 覆盖), 入参 None 时取默认
    ★ v3.1: Embedding 结果缓存，同文本不重复计算
    ★ S3-014: 修复行为偏差 — 新增 return_tracking 参数
      - return_tracking=False (默认): 返回 list[kept] (向后兼容)
      - return_tracking=True: 返回 {"kept": [...], "duplicates": [...], "duplicate_count": N}
        duplicates 保留标记信息（_dedup_action="marked_duplicate"）供上层追踪
    """
    if threshold is None:
        try:
            from src.core.pipeline_config import pipeline_config

            threshold = float(pipeline_config.dedup_similarity_threshold)
        except Exception as _e:
            _log.debug("[dedup] deduplicate_intents 异常: %s", _e, exc_info=True)
            threshold = 0.85
    threshold = float(threshold)

    if not new_intents:
        if return_tracking:
            return {"kept": [], "duplicates": [], "duplicate_count": 0}
        return new_intents
    if not existing_intents:
        if return_tracking:
            return {"kept": new_intents, "duplicates": [], "duplicate_count": 0}
        return new_intents

    # 尝试使用 RAG Engine 的 embedding
    embed_fn = None
    with guard("deduplicate_intents: from src.extensions.rag_engine impor", log=_log, level=logging.INFO):
        from src.extensions.rag_engine import rag_engine

        if hasattr(rag_engine, "_embed_text"):
            embed_fn = rag_engine._embed_text
        elif hasattr(rag_engine, "embed"):
            embed_fn = rag_engine.embed

    if embed_fn is None:
        # ★ S3-014: 关键词去重降级路径也需支持 return_tracking
        keyword_deduped = _dedup_by_keywords(new_intents, existing_intents)
        if return_tracking:
            kept_ids = {c.get("case_id") for c in keyword_deduped}
            dup_list = []
            for c in new_intents:
                if c.get("case_id") not in kept_ids:
                    c["_dedup_action"] = "marked_duplicate"
                    dup_list.append(c)
            return {
                "kept": keyword_deduped,
                "duplicates": dup_list,
                "duplicate_count": len(dup_list),
            }
        return keyword_deduped

    # ★ v3.1: 带缓存的 embed 调用
    def _cached_embed(text: str):
        if not text:
            return None
        if text in _EMBED_CACHE:
            return _EMBED_CACHE[text]
        try:
            vec = embed_fn(text)
        except Exception as _e:
            _log.debug("[dedup] embed 异常: %s", _e, exc_info=True)
            return None
        if vec and len(_EMBED_CACHE) < _EMBED_CACHE_MAX:
            _EMBED_CACHE[text] = vec
        return vec

    duplicates = set()
    for nc in new_intents:
        # v3.0: 语义去重用 title + checks[] 文本
        checks = _parse_checks(nc)
        nc_text = "%s %s" % (
            nc.get("title", ""),
            json.dumps(checks, ensure_ascii=False),
        )
        nc_vec = _cached_embed(nc_text)
        if not nc_vec:
            continue

        for ec in existing_intents:
            if not _same_req_scope(nc, ec):
                continue  # ★ v29: 跨需求用例不参与去重比较（各自验证自己的需求）
            ec_checks = _parse_checks(ec)
            ec_text = "%s %s" % (
                ec.get("title", ""),
                json.dumps(ec_checks, ensure_ascii=False),
            )
            ec_vec = _cached_embed(ec_text)
            if not ec_vec:
                continue

            sim = _cosine_sim(nc_vec, ec_vec)
            if sim > threshold:
                nc_quality = _case_quality_score(nc)
                ec_quality = _case_quality_score(ec)
                if nc_quality <= ec_quality:
                    duplicates.add(nc.get("case_id", ""))
                break

    if duplicates:
        _log.info(
            "  [Dedup] 去重: %d/%d 用例被标记为重复", len(duplicates), len(new_intents)
        )
    # ★ S3-014: 修复行为偏差 — 标记重复并保留引用，返回 kept + duplicates 追踪信息
    kept = []
    dup_list = []
    for c in new_intents:
        cid = c.get("case_id")
        if cid in duplicates:
            c["_dedup_action"] = "marked_duplicate"  # 标记为重复（保留引用）
            dup_list.append(c)
        else:
            kept.append(c)
    if return_tracking:
        return {
            "kept": kept,
            "duplicates": dup_list,
            "duplicate_count": len(dup_list),
        }
    return kept


def dedup_remodel(
    new_intents: list, existing_intents: list, threshold: float = None
) -> dict:
    """
    ★ v3.1: 重新建模专用去重。返回去重结果 + 重复详情。

    Returns:
        {
            "kept": [...],        # 保留的用例（未重复）
            "duplicates": [...],  # 被去重的用例信息
            "all_duplicates": bool  # 是否全部重复
        }
    """
    if not new_intents:
        return {"kept": [], "duplicates": [], "all_duplicates": False}

    deduped = deduplicate_intents(new_intents, existing_intents, threshold)
    deduped_ids = {c.get("case_id", "") for c in deduped}
    dup_info = []
    for nc in new_intents:
        cid = nc.get("case_id", "")
        if cid not in deduped_ids:
            dup_info.append(
                {
                    "case_id": cid,
                    "title": nc.get("title", ""),
                    "reason": "与已有用例相似度超过阈值",
                }
            )

    _log.info(
        "[Dedup-Remodel] 新生成 %d 条，去重后保留 %d 条，重复 %d 条",
        len(new_intents),
        len(deduped),
        len(dup_info),
    )

    return {
        "kept": deduped,
        "duplicates": dup_info,
        "all_duplicates": len(deduped) == 0 and len(new_intents) > 0,
    }


def _dedup_by_keywords(new_intents: list, existing_intents: list) -> list:
    """基于关键词 Jaccard 相似度的回退去重。"""
    duplicates = set()

    def _keywords(intent):
        title = intent.get("title", "")
        return set(w for w in title.lower().split() if len(w) >= 2)

    for nc in new_intents:
        nk = _keywords(nc)
        for ec in existing_intents:
            if not _same_req_scope(nc, ec):
                continue  # ★ v29: 跨需求用例不参与去重比较
            ek = _keywords(ec)
            if not nk or not ek:
                continue
            intersection = nk & ek
            union = nk | ek
            jaccard = len(intersection) / len(union) if union else 0
            if jaccard > 0.7:
                duplicates.add(nc.get("case_id", ""))
                break

    if duplicates:
        _log.info(
            "  [Dedup-Keywords] 去重: %d/%d 用例被移除",
            len(duplicates),
            len(new_intents),
        )
    return [c for c in new_intents if c.get("case_id") not in duplicates]


def _case_quality_score(intent: dict) -> int:
    """v3.0: 计算用例质量分数（越高越好）。基于核心字段 + checks[] 数量 + precision 评分。"""
    score = 0
    risk_level = intent.get("risk_level", "P2")
    if risk_level == "P0":
        score += 3
    elif risk_level == "P1":
        score += 2
    elif risk_level == "P2":
        score += 1

    # 核心字段填充（每字段 2 分，最多 8 分）
    CORE_FIELDS = ["title", "case_type", "risk_level", "required_tracks"]
    score += sum(2 for f in CORE_FIELDS if intent.get(f))

    # checks[] 数量（每条 1 分，最多 12 分）
    checks = _parse_checks(intent)
    score += min(len(checks), 12)

    # precision 奖励：exact 占比
    if checks:
        exact_count = sum(
            1
            for c in checks
            if isinstance(c, dict)
            and str(c.get("precision", "exact")).lower() == "exact"
        )
        exact_ratio = exact_count / len(checks)
        if exact_ratio >= 0.8:
            score += 4
        elif exact_ratio >= 0.5:
            score += 2

    # actions[] 数量奖励
    actions = intent.get("actions") or []
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except Exception as _e:
            _log.debug("[dedup] _case_quality_score 异常: %s", _e, exc_info=True)
            actions = []
    if isinstance(actions, list) and len(actions) >= 3:
        score += 2

    return score


def _cosine_sim(a, b) -> float:
    """计算两个向量的余弦相似度。"""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
