# -*- coding: utf-8 -*-
"""去重 mixin"""

import hashlib
import logging
from src.core.guarded import guard
from ._segment import _HEADING_PATTERN

_log = logging.getLogger("rag_engine")


class ChunkingDedupMixin:
    """跨文档语义去重相关方法。"""

    @staticmethod
    def _section_anchor(text: str) -> str:
        """切片所属章节标题（首个标题行）。不同章节即不同内容单元。"""
        for line in (text or "").split("\n"):
            line = line.strip()
            if line and _HEADING_PATTERN.match(line):
                return line
        return ""

    @staticmethod
    def _deduplicate_chunks(chunks: list[dict]) -> list[dict]:
        """
        跨文档去重 — 精确 + 语义双重去重。

        第一层（SSOT，精确 O(n)）：chunk_id OR text_hash 任一命中即去重。
          - text_hash 用 md5(text.strip().encode())，**跨 chunk_id 去重同文本**
            （修复 _smart_chunk_requirement 单次切片调用产出 4 对同文本不同 ID 的根因）
          - 保留首个出现的切片，后续同 ID/同文本的全部丢弃
        第二层：同类型语义去重 → 文本相似度 > 0.85 时，保留文本更长（信息更丰富）的那个。
          例外：分属不同章节（标题锚点不同）的切片不是重复 —— 需求文档里"2.1/2.2/2.3
          商品子模块"文本高度相似但各自是独立需求，合并会直接丢内容。

        执行效率：精确去重 O(n)，语义去重用字符集 Jaccard 粗筛（>92% 重叠才进语义计算），
        仅在精确去重后同类型 chunk > 1 时才触发语义层。
        """
        if not chunks:
            return chunks

        # ── 第一层：chunk_id OR text_hash 精确去重（单源头） ──
        # ★ 2026-10-23 根因修复：text_hash 让不同 chunk_id 但相同 text 的也被去重
        #   修复的问题：Step2 _smart_chunk_requirement 单次调用产出 req_raw:1/5、2/6、3/7
        #   各对 text 402 字完全相同，导致 Step2 拆出成对重复需求（REQ-026/060 等）
        _EXACT_REMOVE = 0
        seen_ids = {}
        seen_text_hashes = {}
        exact_deduped = []
        for chunk in chunks:
            cid = chunk.get("chunk_id", "")
            text = (chunk.get("text") or "").strip()
            text_hash = hashlib.md5(text.encode()).hexdigest() if text else ""
            if cid and cid in seen_ids:
                _EXACT_REMOVE += 1
                continue
            if text_hash and text_hash in seen_text_hashes:
                _EXACT_REMOVE += 1
                continue
            if cid:
                seen_ids[cid] = True
            if text_hash:
                seen_text_hashes[text_hash] = cid or True
            exact_deduped.append(chunk)
        if _EXACT_REMOVE > 0:
            _log.info(
                "[ChunkingDedupMixin] 精确去重移除 %d / %d 个重复切片 (text_hash)",
                _EXACT_REMOVE, len(chunks),
            )
        if len(exact_deduped) <= 1:
            return exact_deduped

        # ── 第二层：同类型语义去重 ──
        # 按类型分组
        by_type: dict[str, list[dict]] = {}
        for chunk in exact_deduped:
            ctype = chunk.get("type", "unknown")
            by_type.setdefault(ctype, []).append(chunk)

        SEMANTIC_DEDUP_THRESHOLD = 0.85  # 语义相似度阈值
        # ★ 性能护栏：同类型 chunk 过多时，语义两两比较会爆炸（O(n²)），仅保留轻量粗筛
        SEMANTIC_GROUP_LIMIT = 80
        result = []
        semantic_removed = 0
        skipped_semantic_groups = 0

        _semantic_sim = None
        with guard("_deduplicate_chunks: from src.extensions.semantic_index i", log=_log):
            from src.extensions.semantic_index import similarity as _semantic_sim

        for ctype, group in by_type.items():
            if len(group) <= 1:
                result.extend(group)
                continue

            # 对于同类型的 chunks：两两比较语义相似度
            kept = []
            # 按文本长度降序排列 → 优先保留信息量大的
            group.sort(key=lambda c: len(c.get("text", "")), reverse=True)

            # ★ 性能护栏：超大分组跳过语义相似度，避免 O(n²) 拖垮流水线
            use_semantic = (
                _semantic_sim is not None and len(group) <= SEMANTIC_GROUP_LIMIT
            )
            if not use_semantic and len(group) > SEMANTIC_GROUP_LIMIT:
                skipped_semantic_groups += 1
                _log.info(
                    "[RagEngine] 类型 %s 共 %d chunks，超过 %d 阈值，跳过语义去重",
                    ctype,
                    len(group),
                    SEMANTIC_GROUP_LIMIT,
                )

            for chunk in group:
                text = chunk.get("text", "")
                anchor = ChunkingDedupMixin._section_anchor(text)
                is_dup = False
                for existing in kept:
                    existing_text = existing.get("text", "")
                    existing_anchor = ChunkingDedupMixin._section_anchor(existing_text)
                    # 不同章节 → 独立内容单元，不做相似度合并
                    if anchor and existing_anchor and anchor != existing_anchor:
                        continue
                    # 快速精确前缀检查（>80% 文本重叠 → 直接判重，跳过语义计算）
                    min_len = min(len(text), len(existing_text))
                    if min_len > 0:
                        # 简单的字符级 Jaccard 粗筛
                        text_set = set(text[:200])
                        existing_set = set(existing_text[:200])
                        overlap = len(text_set & existing_set)
                        if overlap > 0:
                            quick_sim = overlap / max(len(text_set | existing_set), 1)
                            if quick_sim > 0.92:
                                is_dup = True
                                break
                    # 语义相似度检查（仅在分组大小受控时执行）
                    if use_semantic and len(text) > 20 and len(existing_text) > 20:
                        try:
                            sim = _semantic_sim(text[:500], existing_text[:500])
                            if sim >= SEMANTIC_DEDUP_THRESHOLD:
                                is_dup = True
                                break
                        except Exception as _e:
                            _log.debug("未知: %s", _e, exc_info=True)
                if not is_dup:
                    kept.append(chunk)
                else:
                    semantic_removed += 1

            result.extend(kept)

        if semantic_removed > 0:
            _log.info("[RagEngine] 语义去重移除 %d 个重复chunk", semantic_removed)
        if skipped_semantic_groups > 0:
            _log.info("[RagEngine] %d 个超大分组跳过语义去重", skipped_semantic_groups)

        return result
