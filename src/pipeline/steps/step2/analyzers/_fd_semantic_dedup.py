# -*- coding: utf-8 -*-
"""功能点语义聚合去重（semantic_merge_features）—— 流水线强化专项 T4。

★ 职责：在 feature_driven 已有「(operation, expected_feedback) 精确去重」
  （_deduplicate_triples）之上，补一层**语义级合并**：同义功能点（如「新增商品」
  vs「创建商品」）经同义词/归一化意图聚合为一条，输出合并前后计数 + 可解释合并依据。

★ 复用（design §3 T4 表 / PRD §P1-3）：
  - synonym_library（MySQL 同义词库，core_word + synonym_group）
  - capability_cache（normalize_intent 归一化意图）
  - 无 DB 环境自动降级内置同义词组（零依赖，纯算法可测）。

★ 不变量（对齐 feature_driven 核心原则）：
  - 「不同可观察反馈 = 不同原子需求」：仅当 operation 语义相似 **且**
    expected_feedback 归一化相等/互含时才合并，避免把不同断言的原子需求误并。
  - 纯增量：只合并，不删除既有字段；代表条目保留最完整 source_quote / condition。
"""
from __future__ import annotations
from src.core.guarded import guard

import json
import logging
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any
from src.pipeline.steps.step2.analyzers._fd_labels import _VERB_PREFIXES as _DEFAULT_VERB_PREFIXES  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)

# ── 最近一次语义聚合统计（进程内只读缓存，供前端 /api/quality/semantic-merge/latest 展示）──
_LATEST_MERGE_STATS: dict[str, Any] = {
    "before": 0,
    "after": 0,
    "merged_count": 0,
    "logs": [],
}


def _project_root() -> Path:
    """鲁棒解析项目根（含 src 的祖先目录）。"""
    cur = Path(__file__).resolve().parent
    for _ in range(10):
        if (cur / "src").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return cur


def _meta_path() -> Path:
    """最近一次合并统计持久化文件（src/verification/meta/semantic_merge_latest.json）。"""
    root = _project_root()
    return root / "src" / "verification" / "meta" / "semantic_merge_latest.json"


def _persist_latest_stats(stats: dict[str, Any]) -> bool:
    """把最近一次语义聚合统计原子写入磁盘 json（服务重启后仍可展示）。

    仅持久化「发生过合并」的统计（before>after）；全量聚合也会写入，
    便于前端区分「从未合并」与「本次未合并」。失败仅告警不阻断。
    """
    try:
        from datetime import datetime, timezone

        path = _meta_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(stats)
        payload["timestamp"] = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)
        return True
    except Exception as _e:  # noqa: BLE001
        _log.warning("[_fd_semantic_dedup] 合并统计持久化失败(不阻断): %s", str(_e)[:160])
        return False


def latest_merge_stats() -> dict[str, Any]:
    """返回最近一次语义聚合统计（磁盘持久化优先，进程内缓存兜底）。

    服务重启后仍能读到最近一次合并统计（含 timestamp）；从未执行过
    语义聚合时返回全 0 空统计（前端据此显示「暂无合并记录」）。
    """
    with guard("latest_merge_stats: path = _meta_path()", log=_log):
        path = _meta_path()
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    # 进程内缓存兜底（缓存为空时返回全 0 空统计，保证字段齐全）
    memory = dict(_LATEST_MERGE_STATS)
    if not memory:
        memory = {"before": 0, "after": 0, "merged_count": 0, "logs": []}
    return memory

# ── 内置同义词组（DB 不可用时的零依赖兜底，与 synonym_library 表同构）──
_BUILTIN_SYNONYM_GROUPS: dict[str, list[str]] = {
    "新增": ["新增", "创建", "添加", "录入", "登记"],
    "修改": ["修改", "编辑", "更新", "变更", "调整"],
    "删除": ["删除", "移除", "作废", "注销"],
    "查询": ["查询", "查看", "搜索", "检索", "浏览"],
    "登录": ["登录", "登陆", "登入"],
    "提交": ["提交", "保存", "确认", "发布"],
    "导入": ["导入", "上传", "批量导入"],
    "导出": ["导出", "下载", "批量导出"],
}


# Jaccard 相似度阈值（语义聚类的第 3 层判据）
_SEMANTIC_JACCARD_THRESHOLD = 0.45

# ★ Embedding 余弦相似度阈值（语义聚类的第 1 层判据）
#   bge-large-zh-v1.5 对中文语义区分度高，0.85 可有效合并同义表述
#   （如「执行流水线」≈「执行测试流水线」≈「按步骤执行测试流水线」）
_EMBEDDING_SIMILARITY_THRESHOLD = 0.85

# ★ Embedding 缓存（进程内，避免重复调用 API）
_EMBEDDING_CACHE: dict[str, list[float]] = {}


def _load_verb_prefixes() -> tuple[str, ...]:
    """读取动词前缀（优先配置文件，兜底内置列表）。"""
    with guard("_load_verb_prefixes: import json", log=_log):
        import json
        from pathlib import Path

        config_path = str(
            Path(__file__).resolve().parent.parent.parent.parent.parent
            / "config"
            / "operation_verbs.json"
        )
        if Path(config_path).exists():
            with open(config_path, "r", encoding="utf-8") as f:
                dict_config = json.load(f)
            prefixes: list[str] = []
            for lang in ("zh", "en"):
                prefixes.extend(dict_config.get("verb_prefixes", {}).get(lang, []))
            if prefixes:
                return tuple(prefixes)
    return _DEFAULT_VERB_PREFIXES


def _load_synonym_groups(use_db: bool = True) -> dict[str, list[str]]:
    """加载同义词组：DB synonym_library 优先，缺失时降级内置。

    Returns:
        {core_word: [同义词列表, ...]}（core_word 也在列表中，便于检索）。
    """
    groups: dict[str, list[str]] = {}
    for core, syns in _BUILTIN_SYNONYM_GROUPS.items():
        groups[core] = list(syns)

    if not use_db:
        return groups

    with guard("_load_synonym_groups: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection

        conn = None
        try:
            conn = get_connection()
            rows = conn.execute(
                "SELECT core_word, synonym_group FROM synonym_library ORDER BY id ASC"
            ).fetchall() or []
            for r in rows:
                core = str(r.get("core_word") or "").strip()
                raw = str(r.get("synonym_group") or "").strip()
                if not core or not raw:
                    continue
                syns = [s.strip() for s in re.split(r"[,，;；\s]+", raw) if s.strip()]
                if syns:
                    groups.setdefault(core, []).extend(syns)
                    groups[core] = list(OrderedDict.fromkeys(groups[core]))
        finally:
            if conn:
                with guard("_load_synonym_groups: conn.close()", log=_log):
                    conn.close()
    return groups


def normalize_operation(operation: str) -> str:
    """归一化操作描述：剥离动词前缀 + 小写 + 去空白。

    复用意图归一化（capability_cache.normalize_intent 可用时优先）。
    """
    op = str(operation or "").strip()
    if not op:
        return ""
    with guard("normalize_operation: from src.pipeline.steps.step4.capabi", log=_log):
        from src.pipeline.steps.step4.capability_cache.keys import normalize_intent

        return normalize_intent(op)
    for prefix in _load_verb_prefixes():
        if op.startswith(prefix):
            op = op[len(prefix):]
            break
    return re.sub(r"\s+", "", op).lower()


def _normalize_feedback(text: str) -> str:
    """归一化 expected_feedback（去空白 + 小写）。"""
    return re.sub(r"\s+", "", str(text or "")).strip().lower()


def _synonym_expansion(op: str, groups: dict[str, list[str]]) -> set[str]:
    """把操作文本中的词替换为同义词组展开集合（用于语义相似判定）。"""
    expanded: set[str] = set()
    for core, syns in groups.items():
        for s in syns:
            if s and s in op:
                expanded.update(syns)
    return expanded


# ══════════════════════════════════════════════════
# ★ Embedding-based 语义相似度（长期方案核心）
# ══════════════════════════════════════════════════

def _req_op_text(req: dict[str, Any]) -> str:
    """操作纯文本（语义去重的 embedding 判据：只比较 operation 干什么）。"""
    return str(req.get("operation") or "").strip()


def _req_text(req: dict[str, Any]) -> str:
    """将需求三元组转为嵌入文本：operation + expected_feedback。"""
    op = str(req.get("operation") or "").strip()
    fb = str(req.get("expected_feedback") or "").strip()
    return f"{op}: {fb}"


def _embed_texts(texts: list[str]) -> list[list[float]] | None:
    """批量计算文本嵌入向量（云端 embedding API，带缓存）。

    Returns:
        嵌入向量列表，与 texts 一一对应；失败返回 None。
    """
    if not texts:
        return []

    # 检查缓存
    uncached = []
    uncached_indices = []
    for i, t in enumerate(texts):
        if t in _EMBEDDING_CACHE:
            continue
        uncached.append(t)
        uncached_indices.append(i)

    if uncached:
        try:
            from src.ai.router_module._router import router

            result = router.embed(uncached)
            if result.get("success"):
                embeddings = result.get("data", [])
                for j, t in enumerate(uncached):
                    if j < len(embeddings):
                        _EMBEDDING_CACHE[t] = embeddings[j]
                _log.info(
                    "[_fd_semantic_dedup] 嵌入计算: %d 条文本, model=%s",
                    len(uncached),
                    result.get("model", "?"),
                )
            else:
                _log.warning(
                    "[_fd_semantic_dedup] 嵌入 API 失败: %s，降级为关键词 Jaccard",
                    result.get("error", "?"),
                )
                return None
        except Exception as e:
            _log.warning(
                "[_fd_semantic_dedup] 嵌入不可用: %s，降级为关键词 Jaccard", str(e)[:120]
            )
            return None

    # 从缓存组装结果
    result_embeddings = []
    for t in texts:
        emb = _EMBEDDING_CACHE.get(t)
        if emb is None:
            return None  # 部分缓存未命中，不应发生
        result_embeddings.append(emb)
    return result_embeddings


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _ops_similar_embedding(
    req_a: dict[str, Any],
    req_b: dict[str, Any],
    embeddings: dict[str, list[float]],
) -> tuple[bool, str]:
    """基于 embedding 的 operation 语义相似判定（第 1 层判据）。

    ★ 只比较 operation 文本（「要干什么」），不比较 expected_feedback——
    预期行为是否兼容由 _feedbacks_compatible 在合并处独立强制校验，
    从而守住「不同可观察反馈 = 不同原子需求」不变量（同操作不同断言绝不合并）。
    """
    text_a = _req_op_text(req_a)
    text_b = _req_op_text(req_b)
    emb_a = embeddings.get(text_a)
    emb_b = embeddings.get(text_b)
    if emb_a is None or emb_b is None:
        return False, ""

    sim = _cosine_similarity(emb_a, emb_b)
    if sim >= _EMBEDDING_SIMILARITY_THRESHOLD:
        return True, f"operation embedding 余弦相似度={sim:.3f} ≥ {_EMBEDDING_SIMILARITY_THRESHOLD}"
    return False, ""


def _ops_similar(
    op_a: str, op_b: str, groups: dict[str, list[str]],
    req_a: dict[str, Any] | None = None,
    req_b: dict[str, Any] | None = None,
    embeddings: dict[str, list[float]] | None = None,
) -> tuple[bool, str]:
    """判定两个 operation 是否语义相似，返回 (similar, reason)。

    判据优先级（三层级联）：
      1. embedding 余弦相似度 ≥ 0.85（主判据，长期方案）
      2. 归一化后 operation 完全相同（快速路径）
      3. 同义词组命中（规则兜底）
      4. 关键词 Jaccard ≥ 0.45（规则兜底）
    """
    # ★ 第 1 层：embedding 相似度（主判据）
    if embeddings and req_a and req_b:
        similar, reason = _ops_similar_embedding(req_a, req_b, embeddings)
        if similar:
            return True, reason

    # 第 2 层：归一化完全相同
    a = normalize_operation(op_a)
    b = normalize_operation(op_b)
    if not a or not b:
        return False, ""
    if a == b:
        return True, "归一化后 operation 相同"

    # 第 3 层：同义词组
    exp_a = _synonym_expansion(a, groups)
    exp_b = _synonym_expansion(b, groups)
    if exp_a and exp_b and (exp_a & exp_b):
        common = sorted(exp_a & exp_b)
        return True, "同义词组命中: %s" % "/".join(common)

    # 第 4 层：关键词 Jaccard
    kws_a = set(re.findall(r"[\u4e00-\u9fff]{2,4}|[a-zA-Z]{2,}", a))
    kws_b = set(re.findall(r"[\u4e00-\u9fff]{2,4}|[a-zA-Z]{2,}", b))
    if kws_a and kws_b:
        inter = len(kws_a & kws_b)
        union = len(kws_a | kws_b)
        if union and inter / union >= _SEMANTIC_JACCARD_THRESHOLD:
            return True, "关键词 Jaccard=%.2f ≥ %.2f" % (
                inter / union,
                _SEMANTIC_JACCARD_THRESHOLD,
            )
    return False, ""


def _feedbacks_compatible(fb_a: str, fb_b: str) -> bool:
    """expected_feedback 兼容判据：归一化相等 或 互含（保持原子需求不变量）。"""
    na = _normalize_feedback(fb_a)
    nb = _normalize_feedback(fb_b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def semantic_merge_features(
    reqs: list[dict[str, Any]],
    use_db_synonyms: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """语义聚合去重：相似 operation（同义词/归一化）合并为一条功能点。

    Args:
        reqs: Step2 拆解出的需求三元组列表（每个含 operation / expected_feedback）。
        use_db_synonyms: 是否读取 synonym_library（默认 True；不可用时降级内置）。

    Returns:
        (merged_reqs, stats)：
          merged_reqs: 合并后的需求列表（代表条目 + 被合并条目信息）。
          stats: {before, after, merged_count, logs:[{primary, merged, reason}]}
    """
    reqs = reqs or []
    if len(reqs) <= 1:
        return list(reqs), {
            "before": len(reqs),
            "after": len(reqs),
            "merged_count": 0,
            "logs": [],
        }

    groups = _load_synonym_groups(use_db=use_db_synonyms)

    # ★ 预计算所有需求的 operation 嵌入向量（云端 API，批量一次调用）
    #   只嵌入 operation（要干什么），feedback 兼容性在下方独立强制校验
    embeddings_map: dict[str, list[float]] = {}
    with guard("semantic_merge_features: texts = [_req_op_text(r) for r in re", log=_log):
        texts = [_req_op_text(r) for r in reqs]
        emb_list = _embed_texts(texts)
        if emb_list and len(emb_list) == len(texts):
            embeddings_map = {t: e for t, e in zip(texts, emb_list)}
            _log.info(
                "[_fd_semantic_dedup] 嵌入就绪: %d 条, 将使用 embedding 相似度判据",
                len(embeddings_map),
            )

    clusters: list[list[int]] = []
    cluster_repr: list[int] = []  # 每个聚类的代表索引

    for i, req in enumerate(reqs):
        op_i = str(req.get("operation") or "")
        fb_i = str(req.get("expected_feedback") or "")
        matched = -1
        for ci, repr_idx in enumerate(cluster_repr):
            op_r = str(reqs[repr_idx].get("operation") or "")
            fb_r = str(reqs[repr_idx].get("expected_feedback") or "")
            similar, _reason = _ops_similar(
                op_i, op_r, groups,
                req_a=req, req_b=reqs[repr_idx],
                embeddings=embeddings_map if embeddings_map else None,
            )
            # ★ 不变量：无论是否使用 embedding，operation 语义相似
            #   且 expected_feedback 兼容才合并（不同可观察反馈 = 不同原子需求）
            if similar and _feedbacks_compatible(fb_i, fb_r):
                matched = ci
                break
        if matched >= 0:
            clusters[matched].append(i)
        else:
            clusters.append([i])
            cluster_repr.append(i)

    merged: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []
    for indices in clusters:
        if len(indices) == 1:
            merged.append(reqs[indices[0]])
            continue
        # 代表：字段最完整（operation 最长 + source_quote 非空优先）
        primary_idx = max(
            indices,
            key=lambda idx: (
                len(str(reqs[idx].get("source_quote") or "")),
                len(str(reqs[idx].get("operation") or "")),
            ),
        )
        primary = dict(reqs[primary_idx])
        primary.setdefault("merged_from", [])
        for idx in indices:
            if idx == primary_idx:
                continue
            other = reqs[idx]
            # 合并 condition_expr
            cond_a = str(primary.get("condition_expr") or "")
            cond_b = str(other.get("condition_expr") or "")
            if cond_b and cond_b not in cond_a:
                primary["condition_expr"] = f"{cond_a} 或 {cond_b}" if cond_a else cond_b
            # 记录被合并条目
            other_id = str(other.get("req_id") or f"IDX-{idx}")
            primary["merged_from"].append(other_id)
            logs.append(
                {
                    "primary": str(primary.get("req_id") or f"IDX-{primary_idx}"),
                    "merged": other_id,
                    "reason": _merge_reason(
                        str(primary.get("operation") or ""),
                        str(other.get("operation") or ""),
                        groups,
                    ),
                }
            )
        merged.append(primary)

    stats = {
        "before": len(reqs),
        "after": len(merged),
        "merged_count": len(reqs) - len(merged),
        "logs": logs,
    }
    # ★ 更新进程内最近一次统计 + 持久化到磁盘（服务重启后仍可展示）
    _LATEST_MERGE_STATS.update(stats)
    _persist_latest_stats(stats)
    _log.info(
        "[_fd_semantic_dedup] 语义聚合: %d 条 → %d 条（合并 %d 条）",
        stats["before"],
        stats["after"],
        stats["merged_count"],
    )
    return merged, stats


def _merge_reason(op_a: str, op_b: str, groups: dict[str, list[str]]) -> str:
    """生成可解释合并依据（供前端展示）。"""
    similar, reason = _ops_similar(op_a, op_b, groups)
    if similar:
        return f"operation 语义相似（{reason}）: 「{op_a}」≈「{op_b}」"
    return f"operation 语义相似: 「{op_a}」≈「{op_b}」"
