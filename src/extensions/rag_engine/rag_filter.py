# -*- coding: utf-8 -*-
"""
RAG 10 层过滤管道（v1.0 — 2026-07-12）
=======================================
共享公共方法，Step3（用例生成）和 Step4（检查执行）统一调用。

设计原则：
- 输入：entity_refs（来自 retrieve_and_resolve）+ 需求元数据
- 输出：过滤后的 entity_refs
- 对 RAG 来说，生成用例和执行只是输入和输出的区别——同一套过滤逻辑。

10 层架构：
  L1  操作类型过滤    → retrieve_types（已实现于 engine_core.retrieve + _modeling_engine）
  L2  分数+关键词过滤  → per-type top-3（已实现于 engine_core.retrieve_and_resolve）
  L3  控件预验证       → DOM n-gram 匹配（已实现于 _contexts._build_common_contexts）
  L4  上下文跳过       → UI 操作跳过 data_dict/schema（已实现于 _contexts）
  L5  Trace 过滤       → 操作类型 → trace 部分（已实现于 _modeling_engine._enrich_requirements）
  L6  MMR 多样性       → 最大边际相关性重排序（纯算法，零外部依赖）
  L7  Cross-Encoder    → 交叉编码器重打分（stub，安装 sentence-transformers 可激活）
  L8  RECOMP 压缩      → 抽取式压缩（stub，安装相关模型可激活）
  L9  CRAG 知识条      → 知识条分解过滤（stub）
  L10 LLM-as-Judge     → 轻量 AI 兜底判定（使用现有 ai_client）

调用方式：
  from src.extensions.rag_engine.rag_filter import filter_rag_results
  entity_refs = filter_rag_results(entity_refs, requirement, operation="点击保存按钮")
"""

from __future__ import annotations

import logging
import math
from typing import Any

_log = logging.getLogger("rag_filter")

# ═══════════════════════════════════════════════════════════════
# 管道配置
# ═══════════════════════════════════════════════════════════════

# ★ §4.3 单文件≤800行：L6 MMR + L10 LLM-Judge + 辅助函数拆分到 rag_rerank.py
#   零业务逻辑变更，仅物理位置移动；re-export 保持本模块对外 API 兼容
from src.extensions.rag_engine.rag_rerank import (
    _MMR_LAMBDA,
    _LLM_JUDGE_MAX_CHUNKS,
    _LLM_JUDGE_TIMEOUT,
    _mmr_diversity,
    _text_similarity,
    _llm_judge,
    _judge_batch,
    _build_query_text,
)

# L7 Cross-Encoder: 是否可用（运行时检测）
_cross_encoder_available: bool | None = None

# 最小保留数量：过滤后至少保留 N 个 entity_refs（绝对下限）
_MIN_RETAIN_ABSOLUTE = 3

# 最小保留比例：过滤后至少保留输入的 N%（相对于 _MIN_RETAIN_ABSOLUTE 取大值）
_MIN_RETAIN_RATIO = 0.3


# ═══════════════════════════════════════════════════════════════
# 公共入口
# ═══════════════════════════════════════════════════════════════


def filter_rag_results(
    entity_refs: list[dict],
    requirement: dict | None = None,
    *,
    operation: str = "",
    description: str = "",
    enabled_layers: list[str] | None = None,
    mmr_lambda: float = _MMR_LAMBDA,
    min_retain: int | None = None,
    dry_run: bool = False,
) -> dict:
    """RAG 10 层过滤管道 — Step3/Step4 统一入口。

    Args:
        entity_refs: retrieve_and_resolve 返回的 entity_refs 列表。
                     每个 ref 含 _score、_kw_bonus、_section、_chunk_id 等字段。
        requirement:  需求字典（可选，用于 L7/L10 的语义判定）。
        operation:    操作文本（如"点击保存按钮"），用于 L10 判定。
        description:  需求描述文本。
        enabled_layers: 启用的层列表，默认全部（L1-L5 已在调用方完成，此处 L6-L10）。
        mmr_lambda:   MMR 多样性权重。
        min_retain:   最小保留数量，None=自动计算（max(3, ceil(input*30%))）。
        dry_run:      True=只统计不修改，返回 (entity_refs, stats)。

    Returns:
        dict with keys:
          - entity_refs: 过滤后的 entity_refs 列表
          - stats: 各层统计信息
          - layers_run: 实际执行的层列表
          - min_retain_triggered: 是否触发了最小保留兜底
    """
    if not entity_refs:
        return {"entity_refs": [], "stats": {}, "layers_run": []}

    if enabled_layers is None:
        # 默认 L6-L10（L1-L5 已在调用方完成）
        enabled_layers = [
            "L6_mmr",
            "L7_cross_encoder",
            "L8_recomp",
            "L9_crag",
            "L10_llm_judge",
        ]

    stats: dict[str, dict] = {}
    layers_run: list[str] = []
    refs = list(entity_refs)  # 浅拷贝，避免修改原始数据

    # ── L6: MMR 多样性重排序 ──
    if "L6_mmr" in enabled_layers:
        before = len(refs)
        refs = _mmr_diversity(refs, lambda_param=mmr_lambda)
        after = len(refs)
        stats["L6_mmr"] = {"before": before, "after": after, "removed": before - after}
        layers_run.append("L6_mmr")
        _log.info(
            "[RAGFilter] L6 MMR: %d → %d (移除 %d)", before, after, before - after
        )

    # ── L7: Cross-Encoder 重打分 ──
    if "L7_cross_encoder" in enabled_layers:
        before = len(refs)
        refs = _cross_encoder_rerank(refs, requirement=requirement, operation=operation)
        after = len(refs)
        stats["L7_cross_encoder"] = {
            "before": before,
            "after": after,
            "available": _cross_encoder_available,
        }
        layers_run.append("L7_cross_encoder")
        if _cross_encoder_available:
            _log.info("[RAGFilter] L7 Cross-Encoder: %d → %d", before, after)
        else:
            _log.debug("[RAGFilter] L7 Cross-Encoder: 不可用，跳过")

    # ── L8: RECOMP 压缩 ──
    if "L8_recomp" in enabled_layers:
        before = len(refs)
        refs = _recomp_compress(refs, requirement=requirement, operation=operation)
        after = len(refs)
        stats["L8_recomp"] = {"before": before, "after": after}
        layers_run.append("L8_recomp")
        _log.debug("[RAGFilter] L8 RECOMP: %d → %d (stub)", before, after)

    # ── L9: CRAG 知识条分解 ──
    if "L9_crag" in enabled_layers:
        before = len(refs)
        refs = _crag_decompose(refs, operation=operation)
        after = len(refs)
        stats["L9_crag"] = {"before": before, "after": after, "removed": before - after}
        layers_run.append("L9_crag")
        _log.debug("[RAGFilter] L9 CRAG: %d → %d (stub)", before, after)

    # ── L10: LLM-as-Judge 兜底 ──
    if "L10_llm_judge" in enabled_layers:
        before = len(refs)
        refs = _llm_judge(
            refs, requirement=requirement, operation=operation, description=description
        )
        after = len(refs)
        stats["L10_llm_judge"] = {
            "before": before,
            "after": after,
            "removed": before - after,
        }
        layers_run.append("L10_llm_judge")
        if before != after:
            _log.info(
                "[RAGFilter] L10 LLM-as-Judge: %d → %d (移除 %d)",
                before,
                after,
                before - after,
            )
        else:
            _log.debug("[RAGFilter] L10 LLM-as-Judge: 无需判定或全部通过")

    # ── 最小保留兜底 ──
    # ★ 设计原则：层层过滤是精选，不应被原始噪音污染
    #   - 过滤后 > 0：保留过滤结果（精选优于多量，即使数量少于 min_keep）
    #   - 过滤后 = 0：各层内部保护失效的极端情况，才回退原始 top-N
    original_len = len(entity_refs)
    min_keep = _compute_min_retain(original_len, min_retain)
    min_retain_triggered = False

    if len(refs) == 0 and original_len > 0:
        # 仅当过滤后完全为空，才回退到原始输入 top-N
        _log.warning(
            "[RAGFilter] 过滤后为空: %d → 0, 回退原始 top-%d", original_len, min_keep
        )
        backup = sorted(entity_refs, key=lambda r: r.get("_score", 0), reverse=True)
        refs = backup[:min_keep]
        min_retain_triggered = True
        stats["_min_retain"] = {
            "triggered": True,
            "reason": "empty",
            "min": min_keep,
            "actual": len(refs),
        }
    elif 0 < len(refs) < min_keep:
        # 过滤后有结果但数量少：保留精选结果，不回退原始噪音
        _log.info(
            "[RAGFilter] 过滤后保留精选: %d → %d (少于 min_keep=%d, 但精选优于多量)",
            original_len,
            len(refs),
            min_keep,
        )
        stats["_min_retain"] = {
            "triggered": False,
            "reason": "keep_filtered",
            "filtered": len(refs),
            "min": min_keep,
        }

    return {
        "entity_refs": refs if not dry_run else entity_refs,
        "stats": stats,
        "layers_run": layers_run,
        "min_retain_triggered": min_retain_triggered,
    }


# ═══════════════════════════════════════════════════════════════
# L7: Cross-Encoder 重打分
# ═══════════════════════════════════════════════════════════════
# ★ L6 _mmr_diversity / _text_similarity 已迁移到 rag_rerank.py（§4.3 单文件≤800行）


def _cross_encoder_rerank(
    refs: list[dict],
    requirement: dict | None = None,
    operation: str = "",
) -> list[dict]:
    """Cross-Encoder 重打分。

    原理：bi-encoder（embedding 点积）对语义相似但实际不相关的 chunk 可能给高分。
    Cross-Encoder 将 query 和 chunk 同时输入 BERT，输出真实相关性分数。

    当前状态：stub。安装 sentence-transformers 后自动激活。
    激活方式：pip install sentence-transformers

    Returns:
        重打分后的 entity_refs（分数更新为 Cross-Encoder 分数）。
    """
    global _cross_encoder_available

    if _cross_encoder_available is None:
        _cross_encoder_available = _detect_cross_encoder()

    if not _cross_encoder_available:
        return refs

    try:
        query = _build_query_text(requirement, operation)
        if not query:
            return refs

        model = _get_cross_encoder()
        if model is None:
            return refs

        # 提取 chunk 文本（★ 修复：优先用真实内容，回退标识符）
        texts = []
        for ref in refs:
            content = str(ref.get("_content", "") or ref.get("text", "") or "")
            if not content:
                # 回退：用标识符（总比空字符串好）
                content = str(ref.get("_chunk_id", "") or ref.get("id", "") or "")
            texts.append(content)

        # 构建 (query, chunk) 对
        pairs = [(query, t) for t in texts]

        # Cross-Encoder 预测
        import torch

        with torch.no_grad():
            scores = model.predict(pairs, show_progress_bar=False)

        # 更新分数
        for i, ref in enumerate(refs):
            if i < len(scores):
                ref["_score"] = float(scores[i])
                ref["_cross_encoder_score"] = float(scores[i])

        # 按新分数排序
        refs.sort(key=lambda r: r.get("_score", 0), reverse=True)

        # 过滤低分（低于阈值的移除，阈值可配置）
        try:
            from src.core.pipeline_config import pipeline_config

            threshold = float(
                getattr(pipeline_config, "rag_l7_cross_encoder_threshold", 0.3)
            )
        except Exception:
            threshold = 0.3
        filtered = [r for r in refs if r.get("_score", 0) >= threshold]
        _log.info(
            "[RAGFilter] L7 Cross-Encoder: 阈值=%.2f, 过滤前=%d, 过滤后=%d",
            threshold,
            len(refs),
            len(filtered),
        )
        if filtered:
            return filtered
        return refs[: max(1, len(refs) // 2)]  # 至少保留一半

    except Exception as e:
        _log.debug("[RAGFilter] L7 Cross-Encoder 异常(降级): %s", str(e)[:120])
        return refs


def _detect_cross_encoder() -> bool:
    """检测 sentence-transformers CrossEncoder 是否可用。"""
    try:
        import importlib

        importlib.import_module("sentence_transformers.cross_encoder")
        _log.info("[RAGFilter] L7 Cross-Encoder 已激活")
        return True
    except ImportError:
        _log.debug(
            "[RAGFilter] L7 Cross-Encoder 不可用 (pip install sentence-transformers)"
        )
        return False


def _get_cross_encoder():
    """懒加载 CrossEncoder 模型（中文优化 reranker）。

    ★ 可移植性：模型存放在项目内 models/reranker/ 目录，跨设备/跨用户可用。
    ★ 离线加载：local_files_only=True，不联网。
    """
    import os
    from pathlib import Path

    try:
        from sentence_transformers import CrossEncoder

        # ★ 中文 reranker：BAAI/bge-reranker-base
        #   原ms-marco-MiniLM-L-6-v2是英文模型，中文需求（烟叶配方等）语义匹配失效
        #   bge-reranker-base 专为中文检索优化，约1GB，CPU推理可接受
        #   可通过环境变量 RAG_CROSS_ENCODER_MODEL 切换其他模型
        model_name = os.environ.get("RAG_CROSS_ENCODER_MODEL", "BAAI/bge-reranker-base")
        # ★ 项目内缓存目录（跨设备可移植，不依赖用户级 ~/.cache）
        _cache_dir = str(
            Path(__file__).resolve().parent.parent.parent.parent / "models" / "reranker"
        )
        model = CrossEncoder(
            model_name,
            cache_folder=_cache_dir,
            local_files_only=True,  # ★ 离线加载，不联网
        )
        _log.info("[RAGFilter] L7 Cross-Encoder 模型加载完成 (%s)", model_name)
        return model
    except Exception as e:
        _log.debug("[RAGFilter] L7 Cross-Encoder 模型加载失败: %s", str(e)[:80])
        return None


# ═══════════════════════════════════════════════════════════════
# L8: RECOMP 抽取式压缩
# ═══════════════════════════════════════════════════════════════

# L8 配置：综合分数阈值（低于此值的 chunk 被移除）
_RECOMP_MIN_COMBINED_SCORE = 0.35

# L8 配置：section 权重（按操作类型）
#   原理：embedding 分数可能对"语义相似但类型不匹配"的 chunk 给高分
#   例如：查询烟叶信息 → api_endpoints 和 db_tables 权重 1.0，
#         dom_snapshots 和 figma 权重 0.6（数据操作不需要 UI 信息）
_SECTION_WEIGHTS_DATA = {
    "api_endpoints": 1.0,
    "db_tables": 1.0,
    "dom_snapshots": 0.6,
    "figma_pages": 0.6,
    "figma_nodes": 0.6,
    "figma_components": 0.6,
    "similar_requirements": 0.8,
    "uploaded_docs": 0.7,
}
_SECTION_WEIGHTS_UI = {
    "api_endpoints": 0.6,
    "db_tables": 0.6,
    "dom_snapshots": 1.0,
    "figma_pages": 1.0,
    "figma_nodes": 1.0,
    "figma_components": 1.0,
    "prototype_images": 1.0,
    "similar_requirements": 0.8,
    "uploaded_docs": 0.7,
}

# 操作关键词 → 判断是 UI 还是 DATA
_UI_OP_KEYWORDS = {
    "点击",
    "输入",
    "选择",
    "上传",
    "下载",
    "拖拽",
    "滚动",
    "悬停",
    "聚焦",
    "失焦",
    "click",
    "fill",
    "type",
    "select",
    "upload",
    "download",
    "drag",
    "scroll",
    "hover",
    "focus",
    "blur",
    "勾选",
    "切换",
    "打开",
    "关闭",
}
_DATA_OP_KEYWORDS = {
    "查询",
    "搜索",
    "删除",
    "添加",
    "新增",
    "修改",
    "编辑",
    "更新",
    "导出",
    "导入",
    "query",
    "search",
    "delete",
    "add",
    "create",
    "modify",
    "edit",
    "update",
    "export",
    "import",
    "查看",
    "浏览",
    "审批",
    "驳回",
    "分配",
    "配置",
}


def _recomp_compress(
    refs: list[dict],
    requirement: dict | None = None,
    operation: str = "",
) -> list[dict]:
    """RECOMP 抽取式压缩（v3 纯算法版）。

    原理：用 section 权重对 embedding 分数做二次加权，过滤掉"分数高但类型不匹配"的假阳性 chunk。

    算法（零外部依赖）：
    1. 从 operation 判断操作类型（UI / DATA / MIXED）
    2. 对每个 entity_ref，计算 combined_score = _score × section_weight
    3. combined_score < 阈值的 chunk 被移除

    效果：例如「查询烟叶信息」→ api_endpoints 和 db_tables 权重 1.0，
    dom_snapshots 权重 0.6 → 低分 DOM chunk 被移除。

    参考：RECOMP: Improving Retrieval-Augmented LMs with Compression
          and Selective Augmentation (Xu et al., 2023)
    """
    if not refs or len(refs) <= 2:
        return refs

    # 1. 判断操作类型
    op_lower = (operation or "").lower()
    is_ui = any(kw in op_lower for kw in _UI_OP_KEYWORDS)
    is_data = any(kw in op_lower for kw in _DATA_OP_KEYWORDS)

    if is_ui and not is_data:
        weights = _SECTION_WEIGHTS_UI
        op_type = "UI"
    elif is_data and not is_ui:
        weights = _SECTION_WEIGHTS_DATA
        op_type = "DATA"
    else:
        # 混合或无法判断 → 全部权重 1.0，不做过滤
        return refs

    # 2. 计算 combined_score 并过滤
    kept = []
    dropped = []
    for ref in refs:
        section = str(ref.get("_section", "unknown"))
        score = float(ref.get("_score", 0) or 0)
        weight = weights.get(section, 0.7)  # 未知 section 默认 0.7
        combined = score * weight
        ref["_recomp_combined_score"] = round(combined, 3)
        ref["_recomp_weight"] = weight

        if combined >= _RECOMP_MIN_COMBINED_SCORE:
            kept.append(ref)
        else:
            dropped.append(ref)

    if dropped:
        _log.info(
            "[RAGFilter] L8 RECOMP (%s): 移除 %d 个类型不匹配 chunk (threshold=%.2f)",
            op_type,
            len(dropped),
            _RECOMP_MIN_COMBINED_SCORE,
        )
        for d in dropped:
            _log.debug(
                "  RECOMP drop: %s section=%s score=%.2f weight=%.1f combined=%.2f",
                str(d.get("_chunk_id", ""))[:60],
                d.get("_section", "?"),
                d.get("_score", 0),
                d.get("_recomp_weight", 0),
                d.get("_recomp_combined_score", 0),
            )

    return kept


# ═══════════════════════════════════════════════════════════════
# L9: CRAG 知识条分解
# ═══════════════════════════════════════════════════════════════

# L9 配置：同 section 内 chunk_id 前缀相似度阈值（超过此值视为重复知识条）
_CRAG_DEDUP_SIMILARITY = 0.45


def _crag_decompose(refs: list[dict], operation: str = "") -> list[dict]:
    """CRAG 知识条分解过滤（v2 纯算法版）。

    原理：将同一 section 内的 chunk 按 chunk_id 前缀聚类为"知识条"，
    每个知识条只保留最高分 chunk，去除冗余颗粒。

    算法（零外部依赖）：
    1. 按 _section 分组
    2. 组内按 chunk_id 前 N 字符做前缀聚类（同一表/页面/端点的 chunk 前缀相同）
    3. 每个聚类只保留 _score 最高的 chunk
    4. 跨 section 保留多样性

    效果：防止同一张表/同一个页面的多个 chunk 占据上下文窗口。
    例如：db_col:users.id, db_col:users.name, db_col:users.email → 只保留最高分。

    参考：CRAG: Corrective Retrieval Augmented Generation (Yan et al., 2024)
    """
    if not refs or len(refs) <= 3:
        return refs

    # 1. 按 section 分组
    from collections import defaultdict

    by_section: dict[str, list[dict]] = defaultdict(list)
    for ref in refs:
        section = str(ref.get("_section", "unknown"))
        by_section[section].append(ref)

    result = []
    total_dropped = 0

    for section, group in by_section.items():
        if len(group) <= 2:
            result.extend(group)
            continue

        # 2. 按 chunk_id 前缀聚类
        clusters: dict[str, list[dict]] = defaultdict(list)
        for ref in group:
            chunk_id = str(ref.get("_chunk_id", "") or ref.get("id", "") or "")
            # 提取实体前缀：db_col:users.id → db_col:users
            #              api:GET:/user/list → api:GET:/user
            #              dom:/page/login:1 → dom:/page/login
            prefix = _extract_entity_prefix(chunk_id)
            clusters[prefix].append(ref)

        # 3. 每个聚类只保留最高分
        for prefix, cluster in clusters.items():
            if len(cluster) == 1:
                result.append(cluster[0])
            else:
                # 按 _score 排序，保留最高分
                cluster.sort(key=lambda r: r.get("_score", 0), reverse=True)
                best = cluster[0]
                best["_crag_strip"] = prefix
                best["_crag_merged_count"] = len(cluster)
                result.append(best)
                dropped = len(cluster) - 1
                total_dropped += dropped
                if dropped > 0:
                    _log.debug(
                        "  CRAG merge: %s (%d chunks → 1, kept score=%.2f)",
                        prefix[:60],
                        len(cluster),
                        best.get("_score", 0),
                    )

    if total_dropped > 0:
        _log.info(
            "[RAGFilter] L9 CRAG: 合并 %d 个冗余 chunk (知识条去重)", total_dropped
        )

    return result


def _extract_entity_prefix(chunk_id: str) -> str:
    """从 chunk_id 提取实体前缀用于聚类。

    db_col:users.id     → db_col:users
    db:users            → db:users
    api:GET:/user/list  → api:GET:/user
    api:POST:/user      → api:POST:/user
    dom:/page/login:1   → dom:/page/login
    figma_node:abc:0    → figma_node:abc
    figma_page:xyz      → figma_page:xyz
    """
    # 去掉最后的 :数字 后缀（chunk 分段序号）
    import re

    # 去掉 :数字 结尾
    prefix = re.sub(r":\d+$", "", chunk_id)
    # 对于 db_col:table.column → 取 db_col:table
    if prefix.startswith("db_col:") and "." in prefix:
        prefix = prefix.rsplit(".", 1)[0]
    # 对于 api:METHOD:/path/... → 保留到路径前两段
    if prefix.startswith("api:") and "/" in prefix:
        parts = prefix.split("/")
        if len(parts) > 3:
            prefix = "/".join(parts[:3])
    return prefix


# ═══════════════════════════════════════════════════════════════
# L10: LLM-as-Judge 兜底（★ 已迁移到 rag_rerank.py）
# ═══════════════════════════════════════════════════════════════
# _llm_judge / _judge_batch 通过 re-import 引入，详见文件头部


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _compute_min_retain(total: int, explicit: int | None = None) -> int:
    """计算最小保留数量：max(_MIN_RETAIN_ABSOLUTE, ceil(total * _MIN_RETAIN_RATIO))。

    如果 total 本身小于绝对下限，则保留全部。
    """
    if total <= _MIN_RETAIN_ABSOLUTE:
        return total
    if explicit is not None:
        return max(1, min(explicit, total))
    ratio_min = max(1, int(math.ceil(total * _MIN_RETAIN_RATIO)))
    return max(_MIN_RETAIN_ABSOLUTE, ratio_min)


# ★ _build_query_text 已迁移到 rag_rerank.py（§4.3 单文件≤800行）


# ═══════════════════════════════════════════════════════════════
# Step4 适配：raw chunks（retrieve() 返回）→ entity_refs → 过滤 → chunks
# ═══════════════════════════════════════════════════════════════


def filter_rag_chunks(
    chunks: list[dict],
    requirement: dict | None = None,
    *,
    operation: str = "",
    description: str = "",
    enabled_layers: list[str] | None = None,
) -> list[dict]:
    """Step4 适配入口：对 retrieve() 返回的 raw chunks 应用 L6-L10 过滤。

    retrieve() 返回的 chunk 格式：{chunk_id, score, text, type, meta, ...}
    与 entity_refs 格式不同，此函数做自动转换。

    Args:
        chunks: retrieve() 返回的 chunk 列表。
        requirement: 需求字典。
        operation: 操作文本。
        description: 需求描述。
        enabled_layers: 启用的层列表。

    Returns:
        过滤后的 chunk 列表。
    """
    if not chunks:
        return chunks

    # 转换为 entity_refs 格式
    entity_refs = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        ref = {
            "type": chunk.get("type", ""),
            "id": chunk.get("chunk_id", ""),
            "chunk_id": chunk.get("chunk_id", ""),
            "_section": _section_from_chunk_type(chunk.get("type", "")),
            "_chunk_id": chunk.get("chunk_id", ""),
            "_score": float(chunk.get("score", 0) or 0),
            "_content": str(
                chunk.get("text", "") or chunk.get("content", "") or ""
            ),  # ★ L7 Cross-Encoder 需要
        }
        entity_refs.append(ref)

    if not entity_refs:
        return chunks

    # 应用过滤
    result = filter_rag_results(
        entity_refs,
        requirement=requirement,
        operation=operation,
        description=description,
        enabled_layers=enabled_layers,
    )
    filtered_refs = result["entity_refs"]

    # 转换回 chunks（保留原始 chunk 数据）
    ref_id_set = {r.get("chunk_id", "") for r in filtered_refs}
    filtered_chunks = [c for c in chunks if c.get("chunk_id", "") in ref_id_set]

    if len(filtered_chunks) < len(chunks):
        _log.info(
            "[RAGFilter] Step4 chunks: %d → %d", len(chunks), len(filtered_chunks)
        )

    return filtered_chunks


def _section_from_chunk_type(chunk_type: str) -> str:
    """chunk type → section 名映射。"""
    _map = {
        "api": "api_endpoints",
        "db_table": "db_tables",
        "db_column": "db_tables",
        "db_sample": "db_tables",
        "figma_page": "figma_pages",
        "figma_node": "figma_nodes",
        "figma_component": "figma_components",
        "prototype_image": "prototype_images",
        "requirement": "similar_requirements",
        "dom": "dom_snapshots",
        "feature": "dom_snapshots",
        "feature_page_summary": "dom_snapshots",
    }
    return _map.get(chunk_type, chunk_type)


# ═══════════════════════════════════════════════════════════════
# 便捷函数：完整 10 层管道（含 L1-L5 文档说明）
# ═══════════════════════════════════════════════════════════════
