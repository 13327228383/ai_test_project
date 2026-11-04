# -*- coding: utf-8 -*-
"""功能标签（纯算法聚类）+ 切片持久化 + 完整性检查入口。提取自 feature_driven.py。"""

import json
from src.core.guarded import guard
import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .constants import pipeline_config
from ._fd_completeness import _check_requirement_completeness
from ._fd_chunking import _extract_keywords

_log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════
# ★ 新增(07-05g): Phase4 功能标签（纯算法聚类，仅打标签不合并）
#   目的：给每个三元组附加 feature_label 字段，标识所属功能
#   设计：
#     1. 提取所有三元组的 operation 字段
#     2. 用 Jaccard/TF-IDF 纯算法聚类（相似度≥阈值归为同一功能）
#     3. 每个聚类选最长的 operation 作为功能名（最具代表性）
#     4. 三元组独立保留，只附加 feature_label 字段
#   约束：
#     - 不合并三元组（同功能不同操作是独立原子需求）
#     - 纯算法无 AI 调用
#     - 跨章节归并（不依赖 slice_id 相邻性）
# ══════════════════════════════════════════════════

# 功能标签聚类阈值（基于真实样本P90分布，见模块顶部注释）
_FEATURE_LABEL_JACCARD_THRESHOLD = getattr(
    pipeline_config, "step2_feature_label_jaccard_threshold", 0.20
)
_FEATURE_LABEL_TFIDF_THRESHOLD = getattr(
    pipeline_config, "step2_feature_label_tfidf_threshold", 0.35
)


def _extract_operation_keywords(operation: str) -> list[str]:
    """从 operation 字段提取关键词（用于功能聚类）。

    operation 通常是"用户点击登录按钮"这样的短句，
    提取动词+名词作为功能关键词。
    """
    if not operation:
        return []
    # 复用 _extract_keywords（中文2-4字滑块+英文2字符以上）
    return _extract_keywords(operation)


def _cluster_operations_to_features(
    operations: list[str],
    jaccard_threshold: float = _FEATURE_LABEL_JACCARD_THRESHOLD,
    tfidf_threshold: float = _FEATURE_LABEL_TFIDF_THRESHOLD,
) -> list[list[int]]:
    """对 operation 列表做功能聚类（纯算法，无 AI）。

    短路机制：Jaccard 先判，未命中再 TF-IDF。
    聚类策略：贪心——每个 operation 归到第一个相似的聚类。

    Returns:
        clusters: list[list[int]] — 每个聚类是 operation 的索引列表
    """
    if not operations:
        return []

    n = len(operations)
    # 预计算所有关键词集合
    keyword_sets = [_extract_operation_keywords(op) for op in operations]
    keyword_set_sets = [set(ks) for ks in keyword_sets]

    clusters: list[list[int]] = []
    # 每个聚类的关键词集合（用于和新 operation 比较）
    cluster_keyword_sets: list[set] = []

    for i in range(n):
        op_kw = keyword_set_sets[i]
        if not op_kw:
            # 无关键词的 operation 单独成簇
            clusters.append([i])
            cluster_keyword_sets.append(set())
            continue

        # 找第一个相似的聚类
        matched_cluster_idx = -1
        for ci, ckw in enumerate(cluster_keyword_sets):
            if not ckw:
                continue
            # 第1层：Jaccard
            inter = len(op_kw & ckw)
            union = len(op_kw | ckw)
            jac = inter / union if union else 0.0
            if jac >= jaccard_threshold:
                matched_cluster_idx = ci
                break
            # 第2层：TF-IDF 余弦（用关键词计数向量化）
            # ★ 修复：matched_cluster_idx 初始为 -1，Python 中 bool(-1)=True，原条件恒为 False
            if matched_cluster_idx < 0:
                tf = Counter(keyword_sets[i])
                tf_c = Counter()
                for idx in clusters[ci]:
                    tf_c.update(keyword_sets[idx])
                all_terms = set(tf.keys()) | set(tf_c.keys())
                if not all_terms:
                    continue
                dot = sum(tf.get(t, 0) * tf_c.get(t, 0) for t in all_terms)
                norm_a = math.sqrt(sum(v * v for v in tf.values()))
                norm_b = math.sqrt(sum(v * v for v in tf_c.values()))
                if norm_a == 0 or norm_b == 0:
                    continue
                cosine = dot / (norm_a * norm_b)
                if cosine >= tfidf_threshold:
                    matched_cluster_idx = ci
                    break

        if matched_cluster_idx >= 0:
            clusters[matched_cluster_idx].append(i)
            # 更新聚类的关键词集合（合并）
            cluster_keyword_sets[matched_cluster_idx] = (
                cluster_keyword_sets[matched_cluster_idx] | op_kw
            )
        else:
            clusters.append([i])
            cluster_keyword_sets.append(set(op_kw))

    return clusters


def _select_feature_name(reqs: list[dict[str, Any]]) -> str:
    """从聚类的需求列表中选最具代表性的功能模块名。

    策略（优先级）：
      1. module（章节标题）— 取出现次数最多的非空、非"切片"前缀的 module
      2. 从 operation 中提取核心名词（去掉动词前缀）
      3. 兜底："未命名功能"
    """
    if not reqs:
        return "未命名功能"

    # 策略1：用 module（章节标题）作为功能名
    from collections import Counter

    modules = [r.get("module", "") or "" for r in reqs]
    valid_modules = [m for m in modules if m and not m.startswith("切片")]
    if valid_modules:
        mc = Counter(valid_modules).most_common(1)
        if mc:
            return mc[0][0]

    # 策略2：从 operation 中提取核心名词
    operations = [r.get("operation", "") or "" for r in reqs]
    valid_ops = [op for op in operations if op and op.strip()]
    if valid_ops:
        longest = max(valid_ops, key=len)
        # 去掉常见动词前缀，提取核心名词（从配置文件动态加载）
        _verb_prefixes = _load_verb_prefixes()
        simplified = longest
        for prefix in _verb_prefixes:
            if simplified.startswith(prefix):
                simplified = simplified[len(prefix) :]
                break
        if simplified and len(simplified) >= 2:
            return simplified
        return longest

    return "未命名功能"


# 改进8：动词前缀从配置文件动态加载
_VERB_PREFIXES = []

# 配置缺失时的兜底词表（加载失败两分支共用，勿重复字面量）
_VERB_PREFIXES_FALLBACK = [
    "用户点击",
    "点击",
    "用户输入",
    "输入",
    "用户选择",
    "选择",
    "用户操作",
    "操作",
    "用户提交",
    "提交",
    "用户",
    "系统",
    "进行",
]


def _load_verb_prefixes():
    """从配置文件加载动词前缀列表。"""
    global _VERB_PREFIXES

    if _VERB_PREFIXES:  # 已加载
        return _VERB_PREFIXES

    with guard("_load_verb_prefixes: config_path = str( Path(__file__).re", log=_log, level=logging.ERROR):
        config_path = str(
            Path(__file__).resolve().parent.parent.parent.parent.parent
            / "config"
            / "operation_verbs.json"
        )

        if Path(config_path).exists():
            with open(config_path, "r", encoding="utf-8") as f:
                dict_config = json.load(f)

            # 合并所有语言的动词前缀
            for lang in ["zh", "en"]:
                if lang in dict_config.get("verb_prefixes", {}):
                    _VERB_PREFIXES.extend(dict_config["verb_prefixes"][lang])

            _log.info(
                "[feature_driven] 从配置文件加载动词前缀: %d 个", len(_VERB_PREFIXES)
            )
        else:
            _log.warning(
                "[feature_driven] 配置文件不存在: %s，使用默认动词前缀", config_path
            )

    return _VERB_PREFIXES


def _apply_feature_labels(all_reqs: list[dict[str, Any]]) -> dict[str, Any]:
    """给每个三元组打功能标签（Phase4，纯算法，不合并）。

    ★ 设计要点：
      1. 按 operation 语义聚类（Jaccard/TF-IDF 纯算法）
      2. 每个聚类选最长的 operation 作为 feature_label
      3. 三元组独立保留，只附加 feature_label 字段
      4. 跨章节归并（不依赖 slice_id 相邻性）

    Args:
        all_reqs: 需求列表（每个含 operation 字段）

    Returns:
        {"total_reqs": N, "feature_count": M, "largest_feature_size": K, "single_req_features": L}
    """
    if not all_reqs:
        return {
            "total_reqs": 0,
            "feature_count": 0,
            "largest_feature_size": 0,
            "single_req_features": 0,
        }

    operations = [r.get("operation", "") or "" for r in all_reqs]
    clusters = _cluster_operations_to_features(operations)

    # 为每个聚类选功能名（从 req 列表中取 module/section，而非 operation 文本）
    feature_names = []
    for cluster_indices in clusters:
        cluster_reqs = [all_reqs[i] for i in cluster_indices]
        feature_name = _select_feature_name(cluster_reqs)
        feature_names.append(feature_name)

    # 给每个 req 打标签
    for cluster_idx, cluster_indices in enumerate(clusters):
        feature_label = feature_names[cluster_idx]
        for req_idx in cluster_indices:
            all_reqs[req_idx]["feature_label"] = feature_label
            # 同时记录该功能下的需求总数（供 Step3 参考上下文规模）
            all_reqs[req_idx]["feature_size"] = len(cluster_indices)

    # 统计
    feature_sizes = [len(c) for c in clusters]
    single_req_features = sum(1 for s in feature_sizes if s == 1)
    largest = max(feature_sizes) if feature_sizes else 0

    stats = {
        "total_reqs": len(all_reqs),
        "feature_count": len(clusters),
        "largest_feature_size": largest,
        "single_req_features": single_req_features,
    }

    _log.info(
        "[feature_driven] 功能标签完成: %d 条需求 → %d 个功能 (最大功能 %d 条需求, 单需求功能 %d 个)",
        stats["total_reqs"],
        stats["feature_count"],
        stats["largest_feature_size"],
        stats["single_req_features"],
    )

    return stats


def _persist_slices(
    chunks: list[dict[str, str]],
    all_reqs: list[dict[str, Any]],
    project_name: str,
    version_label: str,
) -> int:
    """★ 持久化切片到 requirement_slices 表（07-05h 新增）。

    将 Step2 内存中的 chunks 持久化，支持关系链路查看到切片级别。

    Args:
        chunks: 切片列表（来自 _smart_chunk_requirement）
        all_reqs: 需求列表（用于关联 feature_label）
        project_name: 项目名
        version_label: 版本标签

    Returns:
        成功保存的切片数（-1 表示失败）
    """
    if not chunks:
        return 0

    try:
        from src.pipeline.utils.requirement_slice_store import requirement_slice_store

        # 构造 slice_id → feature_label 映射
        slice_feature_map = {}
        for req in all_reqs:
            slice_id = req.get("slice_id") or ""
            feature_label = req.get("feature_label") or ""
            if slice_id and feature_label:
                # 同一切片可能提取多个需求，保留第一个非空 feature_label
                if slice_id not in slice_feature_map:
                    slice_feature_map[slice_id] = feature_label

        # 构造切片列表
        slices_to_save = []
        for i, chunk in enumerate(chunks):
            slice_id = chunk.get("chunk_id") or f"req_raw:{i}"
            slices_to_save.append(
                {
                    "slice_id": slice_id,
                    "text": chunk.get("text") or "",
                    "section": chunk.get("section") or "",
                    "slice_index": i,
                    "needs_review": bool(chunk.get("needs_review")),
                    "merge_reasons": chunk.get("merge_reasons") or [],
                    "feature_label": slice_feature_map.get(slice_id, ""),
                    "doc_type": "requirement",
                    "doc_anchor": chunk.get("section") or "",
                    "prev_context": chunk.get("prev_context") or "",
                    "next_context": chunk.get("next_context") or "",
                }
            )

        count = requirement_slice_store.save_slices(
            slices_to_save, project=project_name, version_label=version_label
        )
        if count >= 0:
            _log.info(
                "[feature_driven] 切片持久化完成: %d 片入库 (project=%s, version=%s)",
                count,
                project_name,
                version_label,
            )
        else:
            _log.warning(
                "[feature_driven] 切片持久化失败（不阻断拆解）: %s",
                requirement_slice_store._last_error,
            )
        return count
    except Exception as e:
        _log.warning("[feature_driven] _persist_slices 异常(不阻断): %s", str(e)[:120])
        return -1


def _apply_completeness_check(
    all_reqs: list[dict[str, Any]],
) -> dict[str, Any]:
    """对所有需求应用完整性检查，附加 issues 到每条需求。

    ★ 不阻断拆解：即使有 issues，需求仍正常入库
    ★ issues 存入 req["completeness_issues"]，写入 payload_json

    Returns:
        {"total": N, "with_issues": M, "issue_count": K, "by_type": {...}}
    """
    total = len(all_reqs)
    with_issues = 0
    issue_count = 0
    by_type: dict[str, int] = {}

    for req in all_reqs:
        issues = _check_requirement_completeness(req)
        req["completeness_issues"] = issues
        if issues:
            with_issues += 1
            issue_count += len(issues)
            for iss in issues:
                t = iss["type"]
                by_type[t] = by_type.get(t, 0) + 1

    stats = {
        "total": total,
        "with_issues": with_issues,
        "issue_count": issue_count,
        "by_type": by_type,
    }

    if with_issues > 0:
        _log.info(
            "[feature_driven] 完整性检查: %d/%d 条需求有问题, 共 %d 个 issue (%s)",
            with_issues,
            total,
            issue_count,
            ", ".join(f"{k}={v}" for k, v in by_type.items()),
        )
    else:
        _log.info("[feature_driven] 完整性检查: %d 条需求全部通过", total)

    return stats
