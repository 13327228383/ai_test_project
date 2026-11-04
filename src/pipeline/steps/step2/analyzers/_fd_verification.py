# -*- coding: utf-8 -*-
"""5a 子串匹配 + 去重 + 逆向覆盖检查。提取自 feature_driven.py。

[已复核-DeepSeek-V3 2026-08-16]
B3.2 JSON 解析收敛：``_parse_json_output`` 已改为转发
``src.ai.json_parser.parse_json_response``（唯一真源），
仅在本地做形状归一化（统一为 ``list[dict]``，失败返回 ``[]``），
去重/逆向覆盖等业务语义未变。
"""

import json
import logging
import re
from typing import Any, Optional

from .constants import pipeline_config

try:
    from src.extensions.semantic_index import (
        embed as _semantic_embed,
        cosine as _cosine_sim,
    )
except ImportError:
    _semantic_embed = None
    _cosine_sim = None

_log = logging.getLogger(__name__)

_DEDUP_SIMILARITY_THRESHOLD = getattr(
    pipeline_config, "dedup_similarity_threshold", 0.85
)

# ══════════════════════════════════════════════════
# 0.3 5a 子串匹配（校验 source_quote 在切片中，不是在原文中）
# ══════════════════════════════════════════════════


def _verify_source_quotes_in_chunks(
    requirements: list[dict[str, Any]],
    chunks: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """验证每条需求的 source_quote 在其所属切片中是否存在（子串匹配）。

    ★ 修复(07-05): 原文核对改为校验切片（不是全文 raw_text）
      - 原实现：source_quote 在 raw_text 全文匹配（30万字段，易误匹配）
      - 新实现：source_quote 在对应切片（≤600字）匹配（精准，不误匹配）

    ★ v23 根源修复(2026-07-21) 根因E:
      原: source_quote 只在当前切片内匹配，AI 跨切片摘录会被标记为 unverified，
          导致大量需求被过滤，通过率从"整文档喂AI"的高变"切片独立调用"的0%。
      修: 构建扩展切片（当前切片+前切片+后切片），在扩展切片中匹配。
          通用性: 适用于所有跨切片摘录场景，不预设业务场景。
      防幻觉: 扩展切片仍是事实信息（相邻切片原文），不引入新事实。

    三级信任模型：
      - L1 显式（is_implicit=0）：必须核对 source_quote
      - L2 通用隐性（is_implicit=1）：跳过核对
      - L3 业务隐性（is_implicit=2）：跳过核对
    """
    # 构建 chunk_id → text 映射（含合并切片的原始 ID）
    chunk_text_map = {c["chunk_id"]: c["text"] for c in chunks}
    for c in chunks:
        for orig_id in c.get("merged_chunk_ids", []):
            chunk_text_map[orig_id] = c["text"]

    # ★ v23 根因E修复: 构建 chunk_id → extended_text 映射（当前切片+前切片+后切片）
    #   用于放宽 5a 子串匹配范围，解决跨切片摘录被误判为 unverified 的问题。
    chunk_extended_text_map = {}
    for i, c in enumerate(chunks):
        cid = c["chunk_id"]
        parts = []
        if i > 0:
            parts.append(chunks[i - 1].get("text", ""))
        parts.append(c.get("text", ""))
        if i < len(chunks) - 1:
            parts.append(chunks[i + 1].get("text", ""))
        chunk_extended_text_map[cid] = "\n".join(p for p in parts if p)
        # 含合并切片的原始 ID 也指向同一扩展文本
        for orig_id in c.get("merged_chunk_ids", []):
            chunk_extended_text_map[orig_id] = chunk_extended_text_map[cid]

    passed = 0
    failed = 0
    empty = 0
    skipped_implicit = 0

    for req in requirements:
        is_implicit = req.get("is_implicit", 0)
        if is_implicit in (1, 2):
            req["_verify_status"] = "skipped_implicit"
            skipped_implicit += 1
            continue

        quote = (req.get("source_quote") or "").strip()
        if not quote:
            req["_verify_status"] = "empty_quote"
            empty += 1
            continue

        # ★ 从切片中查找（不是原文），支持合并切片的原始 ID
        slice_id = req.get("slice_id", "")
        chunk_text = chunk_text_map.get(slice_id, "")
        # ★ v23 根因E修复: 优先用扩展切片匹配，找不到再用原切片
        extended_text = chunk_extended_text_map.get(slice_id, chunk_text)

        if not chunk_text and not extended_text:
            # 切片丢失 → 降级为 unverified
            req["_verify_status"] = "unverified"
            failed += 1
            continue

        # 子串匹配（忽略大小写和空白差异）
        quote_compact = re.sub(r"\s+", "", quote.lower())

        # 第一优先：在当前切片中匹配（精准）
        chunk_compact = re.sub(r"\s+", "", chunk_text.lower()) if chunk_text else ""
        if quote_compact in chunk_compact:
            req["_verify_status"] = "passed"
            passed += 1
            continue

        # 第二优先：在扩展切片中匹配（当前+前+后，解决跨切片摘录）
        extended_compact = (
            re.sub(r"\s+", "", extended_text.lower()) if extended_text else ""
        )
        if quote_compact in extended_compact:
            req["_verify_status"] = "passed_extended"
            passed += 1
            continue

        # 第三优先：部分匹配（前50%字符，最少10字符）— 在扩展切片中
        half_len = max(len(quote_compact) // 2, 10)
        if extended_compact and quote_compact[:half_len] in extended_compact:
            req["_verify_status"] = "partial_match"
            passed += 1
        else:
            req["_verify_status"] = "unverified"
            failed += 1

    stats = {
        "total": len(requirements),
        "passed": passed,
        "failed": failed,
        "empty": empty,
        "skipped_implicit": skipped_implicit,
    }
    _log.info(
        "[feature_driven] 切片核对(含扩展): total=%d, passed=%d, failed=%d, empty=%d, skipped_implicit=%d",
        stats["total"],
        stats["passed"],
        stats["failed"],
        stats["empty"],
        stats["skipped_implicit"],
    )
    return requirements, stats


# ══════════════════════════════════════════════════
# 3. JSON 输出解析（_extract_from_chunks 调用）
# ══════════════════════════════════════════════════


def _parse_json_output(content: str) -> list[dict[str, Any]]:
    """解析 AI 输出的 JSON（**转发到唯一真源** + 本地形状归一）。

    整改 B3.2：原本地的「markdown 剥离 + ``raw_decode`` 平衡解析」实现已删除，
    改调 ``src.ai.json_parser.parse_json_response``（能力是原实现的严格超集：
    额外覆盖尾逗号、注释、控制字符、非法转义、截断补全）。

    参数：
        content: LLM 原始输出文本，允许为空 / 非字符串。

    返回：
        ``list[dict]``；解析失败返回 ``[]``（不 raise）—— 与整改前契约一致。

    .. note::
       行为差异（均为增强，无退化）：散文包裹的单个 JSON 对象、尾逗号、
       被截断的 JSON 现在能解析成功，整改前返回 ``[]``。
    """
    if not content or not isinstance(content, str):
        return []
    from src.ai.json_parser import parse_json_response as _canonical

    result = _canonical(content)
    if not result.get("success"):
        _log.debug(
            "[fd_verification] JSON 解析失败: %s", str(result.get("error"))[:120]
        )
        return []
    parsed = result.get("data")
    # ── 形状归一：保持整改前的 list[dict] 契约 ──
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        return [parsed]
    return []


# ══════════════════════════════════════════════════
# 4. 去重：相同 (operation, expected_feedback) 合并
# ══════════════════════════════════════════════════


def _deduplicate_triples(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 (operation, expected_feedback) 去重。

    相同 key → 合并 condition（OR 连接）。
    语义相似度 ≥ 阈值 → 视为相同。
    ★ 三级信任模型：合并时保留 is_implicit 更低（更可信）的 source_quote 和 is_implicit。
    """
    if not requirements:
        return []

    deduped = []
    seen_keys = []  # [(op, fb), ...]
    # ★ embedding 缓存，避免 O(n²) 重复计算
    _embed_cache: dict[str, Optional[list[float]]] = {}

    def _get_vec(text: str) -> Optional[list[float]]:
        if text in _embed_cache:
            return _embed_cache[text]
        vec = None
        if _semantic_embed:
            try:
                vec = _semantic_embed(text)
            except Exception as _e:
                _log.debug("embedding 缓存计算失败: %s", _e, exc_info=True)
        _embed_cache[text] = vec
        return vec

    def _merge_into(existing_idx: int, new_req: dict[str, Any]):
        """将 new_req 合并到 deduped[existing_idx]，优先保留更可信的 source_quote/is_implicit。"""
        existing = deduped[existing_idx]
        # 合并 condition
        existing_cond = existing.get("condition_expr", "")
        new_cond = new_req.get("condition_expr", "")
        if new_cond and new_cond not in existing_cond:
            existing["condition_expr"] = (
                f"{existing_cond} 或 {new_cond}" if existing_cond else new_cond
            )
        # ★ 三级信任：保留 is_implicit 更低（更可信）的 source_quote/is_implicit
        existing_implicit = existing.get("is_implicit", 0)
        new_implicit = new_req.get("is_implicit", 0)
        if new_implicit < existing_implicit:
            existing["source_quote"] = new_req.get("source_quote", "")
            existing["is_implicit"] = new_implicit
            existing["_verify_status"] = new_req.get("_verify_status", "")
        elif new_implicit == existing_implicit:
            # 同级别：若 existing 的 source_quote 为空而 new 有，则取 new
            if (
                not existing.get("source_quote", "").strip()
                and new_req.get("source_quote", "").strip()
            ):
                existing["source_quote"] = new_req.get("source_quote", "")

    for req in requirements:
        op = (req.get("operation") or "").strip()
        fb = (req.get("expected_feedback") or "").strip()
        if not op or not fb:
            deduped.append(req)
            continue

        # 精确匹配
        found = False
        for i, (s_op, s_fb) in enumerate(seen_keys):
            if op == s_op and fb == s_fb:
                _merge_into(i, req)
                found = True
                break

        if not found:
            # 语义相似度匹配（使用 embedding 缓存）
            if _semantic_embed and _cosine_sim:
                op_vec = _get_vec(op)
                fb_vec = _get_vec(fb)
                if op_vec and fb_vec:
                    for i, (s_op, s_fb) in enumerate(seen_keys):
                        s_op_vec = _get_vec(s_op)
                        s_fb_vec = _get_vec(s_fb)
                        if s_op_vec and s_fb_vec:
                            op_sim = _cosine_sim(op_vec, s_op_vec)
                            fb_sim = _cosine_sim(fb_vec, s_fb_vec)
                            if (
                                op_sim >= _DEDUP_SIMILARITY_THRESHOLD
                                and fb_sim >= _DEDUP_SIMILARITY_THRESHOLD
                            ):
                                _merge_into(i, req)
                                found = True
                                break

        if not found:
            seen_keys.append((op, fb))
            deduped.append(req)

    _log.info("[feature_driven] 去重: %d → %d 条", len(requirements), len(deduped))
    return deduped


# -- 需求矛盾检测 -> 已提取到 _contradiction.py --
# 导入: from ._contradiction import detect_contradictions

# ══════════════════════════════════════════════════
# 6. 逆向覆盖检查（基于 RAG 功能索引）
# ══════════════════════════════════════════════════


def _check_reverse_coverage_v2(
    requirements: list[dict[str, Any]],
    project_name: str = "",
    version_label: str = "",
) -> dict[str, Any]:
    """基于 rag_chunks 功能索引的逆向覆盖检查。

    从 rag_chunks 表读取 type='feature' 且 is_active=1 的功能 chunk，
    检查是否都有对应的需求覆盖。未被覆盖的 chunk → 标记为 uncovered（需人工确认是否遗漏）。

    匹配规则（两层）：
      1. feature_id 匹配：chunk meta.feature_id 在需求 feature_id 集合中
      2. 关键词匹配：chunk_text 关键词在需求描述中出现
    """
    uncovered = []
    conn = None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        # ★ 从 rag_chunks 读取 feature 类型 chunk（is_active=1 为当前有效）
        rows = (
            conn.execute(
                "SELECT chunk_id, text, meta "
                "FROM rag_chunks "
                "WHERE scope_type = 'project' AND scope_id = %s "
                "  AND type = 'feature' AND is_active = 1",
                (project_name,),
            ).fetchall()
            if project_name
            else []
        )
    except Exception as e:
        _log.warning("[feature_driven] 逆向覆盖查询失败: %s", str(e)[:100])
        rows = []
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[feature_driven] 关闭数据库连接异常: %s", _e, exc_info=True)

    if not rows:
        return {
            "uncovered": [],
            "stats": {"total_chunks": 0, "covered": 0, "uncovered": 0},
        }

    # 收集所有需求中的 feature_id
    covered_feature_ids = set()
    for req in requirements:
        fid = req.get("feature_id", 0)
        if fid:
            covered_feature_ids.add(fid)

    # 收集所有需求描述的关键词
    all_desc = " ".join(r.get("description", "") for r in requirements).lower()

    for row in rows:
        chunk_id = row.get("chunk_id", "")
        chunk_text = row.get("text", "") or ""
        meta_field = row.get("meta", "") or ""
        try:
            # rag_chunks.meta 是 JSON 列，可能返回 dict 或 str
            if isinstance(meta_field, str):
                meta = json.loads(meta_field) if meta_field else {}
            else:
                meta = meta_field or {}
        except (json.JSONDecodeError, TypeError):
            meta = {}

        # ★ 修复：json.loads 可能返回 str/int/list 等非 dict 类型，确保 meta 是 dict
        if not isinstance(meta, dict):
            meta = {}

        feature_id = meta.get("feature_id", 0)
        if feature_id and feature_id in covered_feature_ids:
            continue

        # 关键词检查：chunk_text 中的关键词是否在需求描述中出现
        chunk_lower = chunk_text.lower()
        covered = False
        for word in chunk_lower.split():
            if len(word) >= 3 and word in all_desc:
                covered = True
                break
        if not covered:
            uncovered.append(
                {
                    "chunk_id": chunk_id,
                    "text": chunk_text[:100],
                    "feature_id": feature_id,
                }
            )

    stats = {
        "total_chunks": len(rows),
        "covered": len(rows) - len(uncovered),
        "uncovered": len(uncovered),
    }
    if uncovered:
        _log.info(
            "[feature_driven] 逆向覆盖: %d/%d 个功能 chunk 未被覆盖",
            len(uncovered),
            len(rows),
        )
    return {"uncovered": uncovered, "stats": stats}
