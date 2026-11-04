# -*- coding: utf-8 -*-
"""
rag_rerank.py — RAG 重排与 LLM 判断模块
==========================================
从 rag_filter.py 拆分（§4.3 单文件≤800行红线）。

★ 安全拆分原则：
  - 零业务逻辑变更，仅物理位置移动
  - 保持所有函数签名、返回值 100% 一致
  - 原文件通过 re-import 兼容

包含：
  - L6 MMR 多样性重排序：_mmr_diversity / _text_similarity
  - L10 LLM-as-Judge 兜底判定：_llm_judge / _judge_batch
  - 辅助函数：_build_query_text
  - 配置常量：_MMR_LAMBDA / _LLM_JUDGE_MAX_CHUNKS / _LLM_JUDGE_TIMEOUT
"""
from __future__ import annotations

import logging

_log = logging.getLogger("rag_filter")

# ═══════════════════════════════════════════════════════════════
# 配置常量
# ═══════════════════════════════════════════════════════════════

# L6 MMR: 多样性权重 λ（0=纯多样性，1=纯相关性，默认 0.7 偏相关性）
_MMR_LAMBDA = 0.7

# L10 LLM-as-Judge: 单次最多判定 chunk 数
_LLM_JUDGE_MAX_CHUNKS = 10

# L10: LLM-as-Judge 超时（秒）
_LLM_JUDGE_TIMEOUT = 15


# ═══════════════════════════════════════════════════════════════
# L6: MMR 多样性重排序
# ═══════════════════════════════════════════════════════════════


def _mmr_diversity(refs: list[dict], lambda_param: float = _MMR_LAMBDA) -> list[dict]:
    """最大边际相关性（MMR）重排序。

    算法：
      1. 按分数排序，取最高分作为第一个选中项。
      2. 迭代：对每个未选中项，计算 MMR = λ·score - (1-λ)·max_sim(已选中项)。
      3. 取 MMR 最高的加入选中集，直到 MMR < 阈值或达到上限。

    效果：去除冗余 chunk（如 3 个 chunk 都说同一件事 → 只保留最佳那个）。

    Args:
        refs: entity_refs 列表，每个含 _score 字段。
        lambda_param: 相关性 vs 多样性权重（0=纯多样性，1=纯相关性）。

    Returns:
        重排序后的 entity_refs 列表（可能减少）。
    """
    if len(refs) <= 2:
        return refs

    # 提取文本用于相似度计算
    texts: list[str] = []
    scores: list[float] = []
    for ref in refs:
        text = str(ref.get("_chunk_id", "") or ref.get("id", "") or "")
        # 也纳入 section 信息增加区分度
        section = str(ref.get("_section", "") or "")
        texts.append(f"{section}:{text}")
        scores.append(float(ref.get("_score", 0) or 0))

    n = len(refs)
    if n <= 2:
        return refs

    # 按分数降序排序
    indexed = list(enumerate(refs))
    indexed.sort(key=lambda x: scores[x[0]], reverse=True)

    selected_indices: list[int] = [indexed[0][0]]  # 最高分直接选中
    remaining = indexed[1:]

    # MMR 阈值：低于此值不纳入
    min_score = max(0.01, min(scores) * 0.5) if scores else 0.01

    for _ in range(min(len(remaining), len(refs) - 1)):
        best_idx = -1
        best_mmr = -1.0

        for idx, _ in remaining:
            # 相关性 = score
            relevance = scores[idx]

            # 多样性 = 1 - max_sim(已选中项)
            max_sim = 0.0
            for sel_idx in selected_indices:
                sim = _text_similarity(texts[idx], texts[sel_idx])
                if sim > max_sim:
                    max_sim = sim

            diversity = 1.0 - max_sim
            mmr = lambda_param * relevance + (1.0 - lambda_param) * diversity

            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = idx

        if best_idx < 0 or best_mmr < min_score:
            break

        selected_indices.append(best_idx)
        remaining = [(i, r) for i, r in remaining if i != best_idx]

    # 按原始分数排序返回
    result = [refs[i] for i in selected_indices]
    result.sort(key=lambda r: r.get("_score", 0), reverse=True)
    return result


def _text_similarity(a: str, b: str) -> float:
    """Jaccard 相似度（基于字符 2-gram）。纯算法，零依赖。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    def _ngrams(s: str, n: int = 2) -> set:
        s = s.lower()
        return {s[i : i + n] for i in range(len(s) - n + 1)}

    set_a = _ngrams(a)
    set_b = _ngrams(b)
    if not set_a or not set_b:
        return 0.0

    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


# ═══════════════════════════════════════════════════════════════
# L10: LLM-as-Judge 兜底判定
# ═══════════════════════════════════════════════════════════════


def _llm_judge(
    refs: list[dict],
    requirement: dict | None = None,
    operation: str = "",
    description: str = "",
) -> list[dict]:
    """LLM-as-Judge 兜底判定。

    原理：当算法无法确定某些 chunk 是否相关时，用轻量 AI 调用判定。
    判定问题："以下 chunk 是否与需求 '<操作>' 相关？仅回答 YES/NO。"

    仅对低置信度 chunk（_score < 0.4）调用，避免浪费 token。

    Returns:
        过滤后的 entity_refs（移除 AI 判定为 NO 的 chunk）。
    """
    # 找出需要判定的低置信度 chunk
    uncertain = [r for r in refs if r.get("_score", 0) < 0.4]
    high_conf = [r for r in refs if r.get("_score", 0) >= 0.4]

    if not uncertain:
        return refs

    query = _build_query_text(requirement, operation)
    if not query:
        query = description or operation or "测试需求"

    # 批量判定：每批最多 _LLM_JUDGE_MAX_CHUNKS 个
    keep_indices: set[int] = set()
    remove_indices: set[int] = set()

    for batch_start in range(0, len(uncertain), _LLM_JUDGE_MAX_CHUNKS):
        batch = uncertain[batch_start : batch_start + _LLM_JUDGE_MAX_CHUNKS]
        batch_results = _judge_batch(batch, query)

        for i, (ref, result) in enumerate(zip(batch, batch_results)):
            global_idx = batch_start + i
            if result:
                keep_indices.add(global_idx)
            else:
                remove_indices.add(global_idx)

    # 保留高置信度 + AI 判定通过的
    filtered_uncertain = [r for i, r in enumerate(uncertain) if i in keep_indices]
    if remove_indices:
        _log.debug(
            "[RAGFilter] L10 LLM-as-Judge: 移除 %d 个低相关 chunk", len(remove_indices)
        )

    return high_conf + filtered_uncertain


def _judge_batch(refs: list[dict], query: str) -> list[bool]:
    """对一批 chunk 进行 LLM 判定。返回 [True/False, ...]。"""
    if not refs:
        return []

    try:
        from src.ai.ai_client import ai_brain

        # 构建判定 prompt
        chunks_desc = []
        for i, ref in enumerate(refs):
            chunk_id = str(ref.get("_chunk_id", "") or ref.get("id", "") or "")[:80]
            section = str(ref.get("_section", "") or "")
            chunks_desc.append(f"[{i}] {section}: {chunk_id}")

        prompt = (
            "你是一个检索质量判定器。请判断以下 RAG 检索到的 chunk 是否与需求相关。\n"
            "需求: %s\n\n"
            "Chunk 列表:\n%s\n\n"
            "请对每个 chunk 回答 YES 或 NO，用 JSON 数组格式返回。\n"
            '示例: ["YES", "NO", "YES"]\n'
            "只返回 JSON 数组，不要其他内容。"
        ) % (query[:200], "\n".join(chunks_desc))

        result = ai_brain._call_api(
            system_prompt="你是一个检索质量判定器。只返回 JSON 数组。",
            user_message=prompt,
            temperature=0.0,
            max_tokens=512,
            caller_name="RAGFilter-LLM-Judge",
            task_type="rag_filter_judge",
            force_cloud=False,
        )

        if not result.get("success"):
            _log.debug(
                "[RAGFilter] L10 LLM 判定失败: %s", str(result.get("error", ""))[:80]
            )
            return [True] * len(refs)  # 失败时全部保留

        # 解析结果
        content = result.get("data", result.get("content", ""))
        if isinstance(content, str):
            import json

            content = content.strip()
            # 提取 JSON 数组
            import re

            match = re.search(r"\[.*?\]", content, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                    if isinstance(parsed, list):
                        return [
                            str(v).strip().upper() == "YES" for v in parsed[: len(refs)]
                        ]
                except (json.JSONDecodeError, ValueError) as _e:
                    _log.debug(
                        "[rag_rerank] LLM-as-Judge 响应解析失败: %s", _e, exc_info=True
                    )

        return [True] * len(refs)  # 解析失败时全部保留

    except Exception as e:
        _log.debug("[RAGFilter] L10 LLM 判定异常: %s", str(e)[:120])
        return [True] * len(refs)  # 异常时全部保留


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _build_query_text(requirement: dict | None, operation: str = "") -> str:
    """从 requirement + operation 构建查询文本。"""
    parts = []
    if operation:
        parts.append(operation)
    if isinstance(requirement, dict):
        desc = requirement.get("description", "") or ""
        if desc and desc != operation:
            parts.append(desc)
        module = requirement.get("module", "") or ""
        if module:
            parts.append(module)
    return " ".join(parts).strip()
