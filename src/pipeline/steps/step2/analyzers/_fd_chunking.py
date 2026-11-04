# -*- coding: utf-8 -*-
"""智能切片 + 多技术融合相似度计算（纯算法）。提取自 feature_driven.py。"""

import logging
from src.core.guarded import guard
import math
import re
from collections import Counter

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

# ── 切片驱动架构常量（与 _fd_extraction.py 保持一致）──
_CHUNK_MAX_CHARS = getattr(pipeline_config, "step2_chunk_max_chars", 600)

# ══════════════════════════════════════════════════
# 0. 智能切片（需求文档原文 → ≤600字切片）
# ══════════════════════════════════════════════════

# ── 需求文档标准模式正则（纯算法语义判定，无AI调用）──
# 标准功能点结构: ### 标题 + **操作步骤** + **预期行为**（+可选 **前置条件**）
_REQ_STEPS_MARKER = re.compile(r"\*\*操作步骤\*\*[:：]?", re.IGNORECASE)
_REQ_EXPECTED_MARKER = re.compile(r"\*\*预期行为\*\*[:：]?", re.IGNORECASE)
_REQ_CONDITION_MARKER = re.compile(
    r"\*\*前置条件\*\*|^\*\*前提", re.IGNORECASE | re.MULTILINE
)
# 步骤列表行: "1. xxx" / "2. xxx"
_REQ_STEP_LINE = re.compile(r"^\d+\.\s+\S", re.MULTILINE)
# 项目符号行: "- xxx"
_REQ_BULLET_LINE = re.compile(r"^[-*]\s+\S", re.MULTILINE)
# H3 标题: ### X.Y 功能名
_REQ_H3_HEADING = re.compile(r"^###\s+\d+\.\d+", re.MULTILINE)
# H2 标题: ## X. 模块名
_REQ_H2_HEADING = re.compile(r"^##\s+\d+", re.MULTILINE)


def _detect_req_pattern_state(text: str) -> dict[str, bool]:
    """检测文本中需求文档模式的状态（纯算法）。

    返回:
        {
            "has_steps": 是否有"**操作步骤**"标记,
            "has_expected": 是否有"**预期行为**"标记,
            "has_condition": 是否有"**前置条件**"标记,
            "ends_with_step": 是否以未完成步骤行结尾,
            "ends_with_bullet": 是否以项目符号行结尾,
            "starts_with_step_line": 是否以步骤行开头（接续上一片）,
            "starts_with_bullet": 是否以项目符号开头,
            "has_h3": 是否包含 H3 标题（### X.Y）,
            "has_h2": 是否包含 H2 标题（## X.）,
            "has_incomplete_steps": 是否有未完成的操作步骤
                （最后一个"**操作步骤**"标记后无"**预期行为**"标记）,
        }
    """
    if not text:
        return {
            k: False
            for k in [
                "has_steps",
                "has_expected",
                "has_condition",
                "ends_with_step",
                "ends_with_bullet",
                "starts_with_step_line",
                "starts_with_bullet",
                "has_h3",
                "has_h2",
                "has_incomplete_steps",
            ]
        }

    text_stripped = text.rstrip()
    lines = text_stripped.split("\n")
    last_line = lines[-1].strip() if lines else ""
    first_line = lines[0].strip() if lines else ""

    # ★ 检测未完成的操作步骤：最后一个"**操作步骤**"标记后无"**预期行为**"
    #   用于判断前片末尾的步骤行是否属于未完成的功能点
    has_incomplete_steps = False
    steps_matches = list(_REQ_STEPS_MARKER.finditer(text))
    if steps_matches:
        last_steps_pos = steps_matches[-1].end()
        after_last_steps = text[last_steps_pos:]
        if "**预期行为**" not in after_last_steps:
            has_incomplete_steps = True

    return {
        "has_steps": bool(_REQ_STEPS_MARKER.search(text)),
        "has_expected": bool(_REQ_EXPECTED_MARKER.search(text)),
        "has_condition": bool(_REQ_CONDITION_MARKER.search(text)),
        "ends_with_step": last_line and bool(_REQ_STEP_LINE.match(last_line)),
        "ends_with_bullet": last_line and bool(_REQ_BULLET_LINE.match(last_line)),
        "starts_with_step_line": first_line and bool(_REQ_STEP_LINE.match(first_line)),
        "starts_with_bullet": first_line and bool(_REQ_BULLET_LINE.match(first_line)),
        "has_h3": bool(_REQ_H3_HEADING.search(text)),
        "has_h2": bool(_REQ_H2_HEADING.search(text)),
        "has_incomplete_steps": has_incomplete_steps,
    }


# ══════════════════════════════════════════════════
# 多技术融合相似度计算（第3-5层，纯算法+本地模型）
# ══════════════════════════════════════════════════

# 相似度阈值（可通过 pipeline_config 配置）
# ★ 调优(07-05f) 基于真实数据样本分析（需求规格说明书 370 对切片 + 项目功能需求 166 对切片）：
#   ┌─────────────┬──────────┬──────────┬──────────┐
#   │ 指标         │ 同H3功能  │ 跨H3边界  │ 跨H2章节  │
#   ├─────────────┼──────────┼──────────┼──────────┤
#   │ Jaccard P90  │ 0.17     │ 0.09     │ 0.03     │
#   │ TF-IDF P90   │ 0.57     │ 0.22     │ 0.09     │
#   │ embed P90    │ 0.83     │ 0.60     │ 0.27     │
#   └─────────────┴──────────┴──────────┴──────────┘
#   阈值取"同功能 P90 与跨 H3 P90 之间"作为分界点：
#   - Jaccard 0.15：高于跨H3 P90(0.09)，接近同功能 P90(0.17)
#   - TF-IDF 0.40：高于跨H3 P90(0.22)，低于同功能 P90(0.57)，留余量
#   - embed 0.75：高于跨H3 P90(0.60)，低于同功能 P90(0.83)
#
# ⚠️ 旧值 0.3/0.5/0.75 的问题：
#   Jaccard=0.3 远高于同功能 P90(0.17)，第3层命中率仅 2-3%，形同虚设
#   TF-IDF=0.5 接近同功能 P90(0.57)，命中率不足
#   embed=0.75 设置合理（保持不变）
_JACCARD_THRESHOLD = getattr(pipeline_config, "step2_chunk_jaccard_threshold", 0.15)
_TFIDF_THRESHOLD = getattr(pipeline_config, "step2_chunk_tfidf_threshold", 0.40)
_EMBED_THRESHOLD = getattr(pipeline_config, "step2_chunk_embed_threshold", 0.75)


def _extract_keywords(text: str) -> list[str]:
    """提取关键词：中文2-4字符 + 英文2字符以上单词。

    用于 Jaccard 和 TF-IDF 相似度计算。
    """
    if not text:
        return []
    # 中文：连续2-4个中文字符
    zh_tokens = re.findall(r"[一-鿿]{2,4}", text)
    # 英文：2个字符以上的单词（小写化）
    en_tokens = re.findall(r"[a-zA-Z]{2,}", text.lower())
    return zh_tokens + en_tokens


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    """第3层：Jaccard关键词重叠（纯算法，无模型依赖）。

    公式：|A∩B| / |A∪B|
    速度：O(n·m)，毫秒级
    """
    words_a = set(_extract_keywords(text_a))
    words_b = set(_extract_keywords(text_b))
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union) if union else 0.0


def _tfidf_cosine_similarity(text_a: str, text_b: str) -> float:
    """第4层：TF-IDF余弦相似度（纯算法，无模型依赖）。

    参考 deployment/matcher/phase2.py 的实现。
    速度：O(n·m)，毫秒级
    """
    if not text_a or not text_b:
        return 0.0

    tokens_a = _extract_keywords(text_a)
    tokens_b = _extract_keywords(text_b)

    if not tokens_a or not tokens_b:
        return 0.0

    all_tokens = set(tokens_a + tokens_b)
    if not all_tokens:
        return 0.0

    # 简化的IDF（单文档对比较，所有词权重相同）
    idf = {t: 1.0 for t in all_tokens}

    def _tfidf_vector(tokens):
        tf = Counter(tokens)
        total = len(tokens)
        vec = {}
        for term, count in tf.items():
            vec[term] = (count / total) * idf.get(term, 1.0)
        return vec

    vec_a = _tfidf_vector(tokens_a)
    vec_b = _tfidf_vector(tokens_b)

    # 余弦相似度
    dot = sum(vec_a.get(t, 0) * vec_b.get(t, 0) for t in all_tokens)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def _disk_embedding_similarity(text_a: str, text_b: str) -> float:
    """第5层：disk embedding余弦相似度（本地模型，无AI API调用）。

    用 semantic_index 模块的 embed + cosine 函数。
    速度：O(n)，秒级（需要本地模型推理）
    """
    if not _semantic_embed or not _cosine_sim:
        return 0.0
    with guard("_disk_embedding_similarity: va = _semantic_embed(text_a)", log=_log):
        va = _semantic_embed(text_a)
        vb = _semantic_embed(text_b)
        if va and vb:
            return max(0.0, min(1.0, _cosine_sim(va, vb)))
    return 0.0


def _smart_merge_related_chunks(
    chunks: list[dict[str, str]], max_chars: int
) -> list[dict[str, str]]:
    """智能合并关联切片（多技术融合，分层判定）。

    ★ 解决问题：按章节/语句切片可能切断"操作步骤+预期行为"等需求文档标准模式

    分层判定（短路机制，从快到慢）：
      第1层 模式连续性（纯算法，O(n)）：**操作步骤**+**预期行为**跨片 → 必须合并（无需审核）
      第2层 标题层级（纯算法，O(n)）：不同H3功能点 → 不合并
      第3层 Jaccard关键词重叠（纯算法，O(n·m)）：词集合重叠≥0.15 → 合并（需人工审核）
      第4层 TF-IDF余弦相似度（纯算法，O(n·m)）：加权余弦≥0.40 → 合并（需人工审核）
      第5层 disk embedding余弦（本地模型，O(n)）：向量余弦≥0.75 → 合并（需人工审核）
      ★ 阈值依据：真实样本（538切片对）P90分布，见模块顶部注释

    ★ 方案A(07-05e): 第3-5层相似度判定触发的合并，从其中提取的需求标记为
      review_status=pending_review（强制人工审核，即使置信度≥80也不自动approved）
      原因：相似度判定是概率性判断，有误判风险，需人工确认合并是否合理
      第1-2层（规则判定）确定性强，不需人工审核

    约束：合并后不超过 max_chars（§7 AI输入≤600字硬约束）

    Returns:
        合并后的切片列表（chunk_id 保持原第一片的 id）
    """
    if len(chunks) <= 1:
        return chunks

    # ★ v3.0 (2026-07-10): 收紧上限为 max_chars（600字），符合 §7 AI输入≤600字约束
    #   原 2*max_chars=1200 字导致 AI 输入超限、上下文碎片化
    #   模式跨片合并逻辑保留，但合并后严格不超过 max_chars
    _HARD_LIMIT = max_chars

    merged: list[dict[str, str]] = [dict(chunks[0])]
    merge_count = 0

    for curr in chunks[1:]:
        prev = merged[-1]
        should_merge = False
        reason = ""

        combined_len = len(prev["text"]) + 1 + len(curr["text"])

        # 硬上限检查：即使模式跨片，合并后不超过 2*max_chars
        if combined_len > _HARD_LIMIT:
            merged.append(dict(curr))
            continue

        prev_pat = _detect_req_pattern_state(prev["text"])
        curr_pat = _detect_req_pattern_state(curr["text"])

        # ★ 规则1a：操作步骤 + 预期行为 跨片（最常见模式切断）
        #   前片有"**操作步骤**"无"**预期行为**"
        #   后片以"**预期行为**"开头 或 包含"**预期行为**"且无"**操作步骤**"
        if (
            prev_pat["has_steps"]
            and not prev_pat["has_expected"]
            and (curr_pat["has_expected"] and not curr_pat["has_steps"])
        ):
            should_merge = True
            reason = "操作步骤+预期行为跨片"

        # ★ 规则1b：步骤列表跨片
        #   前片以步骤行结尾（"3. xxx"）且前片有"未完成的操作步骤"
        #   （最后一个"**操作步骤**"标记后无"**预期行为**"）
        #   后片以步骤行开头（"4. xxx"）或"**预期行为**"开头
        #   ★ 用 has_incomplete_steps 替代 not has_expected（更精准）
        elif (
            prev_pat["ends_with_step"]
            and prev_pat["has_incomplete_steps"]
            and (curr_pat["starts_with_step_line"] or curr_pat["has_expected"])
        ):
            should_merge = True
            reason = "步骤列表跨片"

        # ★ 规则1c：前置条件 + 操作步骤 跨片
        elif (
            prev_pat["has_condition"]
            and not prev_pat["has_steps"]
            and curr_pat["has_steps"]
        ):
            should_merge = True
            reason = "前置条件+操作步骤跨片"

        # ★ 规则1d：预期行为项目符号跨片
        #   前片以项目符号结尾（"- xxx"）且前片最后有"**预期行为**"（不是未完成的步骤）
        #   后片以项目符号开头（"- xxx"）且无"**操作步骤**"（不是新功能点）
        #   ★ 约束：后片没有 H3 标题（避免合并不同功能点）
        #   ★ 约束：前片没有未完成的操作步骤（避免与规则1b冲突）
        elif (
            prev_pat["has_expected"]
            and not prev_pat["has_incomplete_steps"]
            and prev_pat["ends_with_bullet"]
            and curr_pat["starts_with_bullet"]
            and not curr_pat["has_steps"]
            and not curr_pat["has_h3"]
        ):
            should_merge = True
            reason = "预期行为列表跨片"

        # ★ 不合并规则：不同 H3 功能点（2.1 vs 2.2 = 不同场景 = 不同需求）
        #   如果当前片以 H3 标题开头，且前片也有 H3 标题（不同功能点），不合并
        #   但模式跨片（规则1a-1d 同功能步骤被切断）已经触发的，仍然合并
        if should_merge and curr_pat["has_h3"]:
            # 当前片有新的 H3 标题 → 这是新功能点
            # 但如果前片有未完成的操作步骤，仍需合并完成当前功能
            if prev_pat["has_incomplete_steps"] and (
                curr_pat["starts_with_step_line"] or curr_pat["has_expected"]
            ):
                # 前片有未完成的操作步骤 → 合并完成当前功能
                pass
            elif (
                prev_pat["has_steps"]
                and not prev_pat["has_expected"]
                and curr_pat["has_expected"]
                and not curr_pat["has_steps"]
            ):
                # 规则1a：前片的操作步骤等的是这个预期行为 → 合并
                pass
            else:
                # 新 H3 功能点，且无跨片模式 → 不合并
                should_merge = False
                reason = ""

        # ══════════════════════════════════════════════════
        # ★ 第3-5层：相似度判定（当第1-2层未命中时启用）
        #   短路机制：第3层命中就不算第4层，依次类推
        #   仅当 should_merge=False 且合并后不超过 _HARD_LIMIT 时才计算
        # ══════════════════════════════════════════════════
        if (
            not should_merge and combined_len <= _HARD_LIMIT and not curr_pat["has_h3"]
        ):  # 后片有新H3功能点时不合并

            # ★ 第3层：Jaccard关键词重叠（纯算法，毫秒级）
            with guard("_smart_merge_related_chunks: jac_sim = _jaccard_similarity(prev['", log=_log):
                jac_sim = _jaccard_similarity(prev["text"], curr["text"])
                if jac_sim >= _JACCARD_THRESHOLD:
                    should_merge = True
                    reason = f"Jaccard相似度={jac_sim:.2f}≥{_JACCARD_THRESHOLD}"

            # ★ 第4层：TF-IDF余弦相似度（纯算法，毫秒级）
            if not should_merge:
                with guard("_smart_merge_related_chunks: tfidf_sim = _tfidf_cosine_similarity", log=_log):
                    tfidf_sim = _tfidf_cosine_similarity(prev["text"], curr["text"])
                    if tfidf_sim >= _TFIDF_THRESHOLD:
                        should_merge = True
                        reason = f"TF-IDF余弦={tfidf_sim:.2f}≥{_TFIDF_THRESHOLD}"

            # ★ 第5层：disk embedding余弦相似度（本地模型，秒级）
            #   仅当第3-4层都未命中时启用（避免不必要的模型推理）
            # ★ 性能修复(07-08): 切片数>100时跳过第5层（O(n)但秒级/次，大量切片总耗时长）
            if not should_merge and len(chunks) <= 100:
                with guard("_smart_merge_related_chunks: embed_sim = _disk_embedding_similari", log=_log):
                    embed_sim = _disk_embedding_similarity(prev["text"], curr["text"])
                    if embed_sim >= _EMBED_THRESHOLD:
                        should_merge = True
                        reason = (
                            f"disk embedding余弦={embed_sim:.2f}≥{_EMBED_THRESHOLD}"
                        )

        if should_merge:
            # ★ 修复(07-05e): 第3-5层相似度判定触发的合并都标记 needs_review
            #   第1-2层（模式连续性+H3标题保护）是规则判定，确定性强，不需审核
            #   第3层（Jaccard）、第4层（TF-IDF）、第5层（embed）是概率性判定，有误判风险，需人工确认
            is_similarity_merge = any(
                k in reason for k in ("Jaccard", "TF-IDF", "disk embedding")
            )
            merged[-1] = {
                "chunk_id": prev["chunk_id"],
                "text": prev["text"] + "\n" + curr["text"],
                "section": prev.get("section", ""),
                # ★ 标记：第3-5层相似度判定触发的合并，从其中提取的需求需要人工审核
                "needs_review": prev.get("needs_review", False) or is_similarity_merge,
                # ★ 合并原因（用于日志和审计）
                "merge_reasons": prev.get("merge_reasons", [])
                + ([reason] if reason else []),
            }
            merge_count += 1
            _log.debug(
                "[feature_driven] 合并切片: %s + %s (%s, %d→%d字%s)",
                prev["chunk_id"],
                curr.get("chunk_id", ""),
                reason,
                len(prev["text"]),
                len(merged[-1]["text"]),
                ", 标记需人工审核" if is_similarity_merge else "",
            )
        else:
            merged.append(dict(curr))

    if merge_count > 0:
        _log.info(
            "[feature_driven] 智能合并: %d 片 → %d 片 (合并 %d 处跨片模式)",
            len(chunks),
            len(merged),
            merge_count,
        )

    return merged


def _smart_chunk_requirement(
    raw_text: str, max_chars: int = _CHUNK_MAX_CHARS
) -> list[dict[str, str]]:
    """智能切片：需求文档原文按章节切，超长二次切，每片≤max_chars 字。

    策略（与 Figma/Upload 一致的层级切片，复用 SmartSegmentMixin）：
      1. 先按章节标题切（_segment_by_outline）
      2. 超长章节用 _smart_segment 二次切（段落→行→句子→短语，禁止硬截断）
      3. 每片≤max_chars 字

    返回:
        [{"chunk_id": "req_raw:0", "text": "...", "section": ""}, ...]
    """
    if not raw_text or not raw_text.strip():
        return []

    try:
        from src.extensions.rag_engine.chunking_mixin_mixins._segment import (
            SmartSegmentMixin,
        )
    except ImportError:
        # 降级：简单按段落切
        paragraphs = [x for p in raw_text.split("\n\n") if (x := p.strip())]
        chunks = []
        for i, p in enumerate(paragraphs):
            if len(p) > max_chars:
                p = p[:max_chars]
            chunks.append({"chunk_id": f"req_raw:{i}", "text": p, "section": ""})
        return chunks

    # 1. 按章节切（_segment_by_outline 内部超长会调 _smart_segment 二次切）
    sections = SmartSegmentMixin._segment_by_outline(raw_text, max_len=max_chars)
    if not sections:
        # 降级：直接用 _smart_segment
        sections = SmartSegmentMixin._smart_segment(raw_text, max_len=max_chars)

    if not sections:
        # 最终降级：按段落切
        paragraphs = [x for p in raw_text.split("\n\n") if (x := p.strip())][:20]
        sections = paragraphs

    # ★ 1.5 合并过短的切片（保证上下文完整，减少 AI 调用次数）
    #   短于 max_chars/3 的段会合并到前一段（合并后不超过 max_chars）
    #   解决问题：按语句/标题切片导致上下文断裂
    if sections and len(sections) > 1:
        sections = SmartSegmentMixin._merge_short_segments(
            sections, min_len=max_chars // 3, max_len=max_chars
        )

    # ★ 1.6 智能合并跨片模式（纯算法语义判定，无AI调用）
    #   先组装成 chunks 结构，再用模式连续性规则合并
    #   解决问题：操作步骤+预期行为被切断、步骤列表跨片
    _pre_chunks = []
    for i, text in enumerate(sections):
        if text and text.strip():
            _pre_chunks.append(
                {
                    "chunk_id": f"req_raw:pre:{i}",
                    "text": text.strip(),
                    "section": "",
                }
            )

    if len(_pre_chunks) > 1:
        _merged_pre = _smart_merge_related_chunks(_pre_chunks, max_chars)
        sections = [c["text"] for c in _merged_pre if c["text"].strip()]

    # 2. 组装结果 + 超长兜底二次切
    # ★ 修复(07-05c): 模式跨片合并后的片不再二次切（避免合并了又被切回去）
    #   - ≤ 2*max_chars 的片：保留原样（可能是模式跨片合并的，必须完整）
    #   - > 2*max_chars 的片：二次切（异常超长，硬上限保护）
    # ★ 删除 [:max_chars] 硬截断（用户明确禁止硬截断）
    _HARD_LIMIT = max_chars * 2
    # ★ 章节标题提取：从每片text开头匹配标题，维护游标向下传递
    import re as _re

    _heading_pat = _re.compile(
        r"(?:^|\n)\s*"
        r"(?:#{1,3}\s+(.+)"
        r"|第[一二三四五六七八九十百0-9]+[章节部分][：:\s]*(.+)"
        r"|[0-9]+\.[0-9.]*\s+(.+)"
        r"|【([^】]+)】\s*"
        r"|\[([^\]]+)\]\s*)",
        _re.MULTILINE,
    )
    _current_section = ""
    chunks = []
    for i, text in enumerate(sections):
        if not text or not text.strip():
            continue
        text = text.strip()

        # ★ 根因修复(2026-10-23): 扫描所有 H2 标题（不是只 match 第一个）
        #   _merge_short_segments 合并短章节后，一个 chunk 里含多个原始 H2
        #   section 仍保留首 H2（向后兼容），child_sections 记录全部子章节
        _all_headings = []
        for hm in _heading_pat.finditer(text):
            _ht = next((g for g in hm.groups() if g), None)
            if _ht:
                _all_headings.append(_ht.strip())
        _child_sections = list(dict.fromkeys(_all_headings))  # 去重保序

        # section 仍取首 H2（向后兼容，_resolve_module_for_chunk 还需要它）
        if _child_sections:
            _current_section = _child_sections[0]
        _section = _current_section

        if len(text) > _HARD_LIMIT:
            # 异常超长（>2*max_chars）才二次切，用智能分段（禁止硬截断）
            sub_sections = SmartSegmentMixin._smart_segment(text, max_len=max_chars)
            if sub_sections:
                for j, sub in enumerate(sub_sections):
                    if sub and sub.strip():
                        chunks.append(
                            {
                                "chunk_id": f"req_raw:{i}:{j}",
                                "text": sub.strip(),
                                "section": _section,
                                "child_sections": _child_sections,
                            }
                        )
            else:
                # 智能分段失败，保留原片（不超过 _HARD_LIMIT）
                chunks.append(
                    {
                        "chunk_id": f"req_raw:{i}",
                        "text": text,
                        "section": _section,
                        "child_sections": _child_sections,
                    }
                )
        else:
            # ≤ 2*max_chars：保留原样（可能是模式跨片合并的完整功能点）
            chunks.append(
                {
                    "chunk_id": f"req_raw:{i}",
                    "text": text,
                    "section": _section,
                    "child_sections": _child_sections,
                }
            )

    # ★ 改进3 (L3): 为每个切片添加前后文上下文（各保留前1段和后1段）
    #   上下文不参与三元组提取，仅供审核参考
    for i, chunk in enumerate(chunks):
        prev_context = ""
        next_context = ""

        # 前文：取前一个切片的最后100字
        if i > 0:
            prev_text = chunks[i - 1].get("text", "")
            if prev_text:
                prev_context = prev_text[-100:] if len(prev_text) > 100 else prev_text

        # 后文：取后一个切片的前100字
        if i < len(chunks) - 1:
            next_text = chunks[i + 1].get("text", "")
            if next_text:
                next_context = next_text[:100] if len(next_text) > 100 else next_text

        chunk["prev_context"] = prev_context
        chunk["next_context"] = next_context

    # ★ 根因修复：切片器单次调用可能产出同文本不同 chunk_id 的重复片
    #   SSOT 去重入口，所有靶子受益
    try:
        from src.extensions.rag_engine.chunking_mixin_mixins._dedup import ChunkingDedupMixin
        chunks = ChunkingDedupMixin._deduplicate_chunks(chunks)
    except Exception as _e:
        _log.warning("[_smart_chunk_requirement] dedup fallback: %s", str(_e)[:100])

    _log.info(
        "[feature_driven] 智能切片: %d 字 → %d 片 (平均 %d 字/片)",
        len(raw_text),
        len(chunks),
        sum(len(c["text"]) for c in chunks) // max(len(chunks), 1),
    )
    return chunks
