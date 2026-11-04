# -*- coding: utf-8 -*-
from __future__ import annotations

"""
结构指标置信度评分 (Structural Confidence Scorer)
===================================================
零 AI 调用，基于需求的结构质量指标计算置信度。

替代旧的多视角交叉验证 + 红队辩论，置信度靠好方法不靠堆AI调用量。

评分维度:
  - source_traceable:      source_quote 非空 → 能追溯到原文
  - source_verified:       source_quote 在 raw_text 中真实存在
  - section_covered:       所在段落至少被1条需求覆盖（逆向覆盖通过）
  - uniqueness:            与其他需求 Jaccard 重叠低 → 不冗余
  - description_specific:  描述足够具体（长度 > 20字）

输出字段完全兼容现有 evidence/confidence.py 的接口:
  - _evidence_confidence:     0.0 ~ 1.0
  - _confidence_level:        "HIGH" | "MEDIUM" | "LOW"
  - _ai_raw_confidence:       AI 自身的置信度（若有）
  - _evidence_verdict:        "verified" | "needs_review" | "uncertain"
  - _evidence_calibration_samples: 校准样本数
  - _evidence_cold_start:     是否冷启动
"""

import logging
import re as _re
from typing import Any, Set

_log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 权重配置（可通过环境变量覆盖）
# ═══════════════════════════════════════════════════════════════

_WEIGHT_SOURCE_TRACEABLE = 0.30  # 能否追溯到原文
_WEIGHT_SOURCE_VERIFIED = 0.25  # 引文是否真实存在
_WEIGHT_SECTION_COVERED = 0.20  # 所在段落是否被覆盖
_WEIGHT_UNIQUENESS = 0.15  # 是否与其他需求不重叠
_WEIGHT_SPECIFICITY = 0.10  # 描述是否具体

# 置信度档位阈值
_HIGH_THRESHOLD = 0.85
_MEDIUM_THRESHOLD = 0.60


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════


def _extract_2gram_tokens(text: str) -> Set[str]:
    """提取中文 2-gram 特征。"""
    tokens = set()
    if not text:
        return tokens
    for i in range(len(text) - 1):
        bigram = text[i : i + 2]
        if _re.match(r"[一-鿿]{2}$", bigram):
            tokens.add(bigram)
    for m in _re.finditer(r"[a-zA-Z_]\w{2,}", text):
        tokens.add(m.group().lower())
    return tokens


def _jaccard(a: Set, b: Set) -> float:
    """Jaccard 相似度。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


def _compute_uniqueness(req: dict[str, Any], all_reqs: list[dict[str, Any]]) -> float:
    """计算需求与其他需求的最大 Jaccard 重叠度，返回去重得分（不重叠=1.0）。"""
    desc = req.get("description", "")
    tokens = _extract_2gram_tokens(desc)
    if not tokens:
        return 1.0

    max_overlap = 0.0
    for other in all_reqs:
        if other is req:
            continue
        other_desc = other.get("description", "")
        other_tokens = _extract_2gram_tokens(other_desc)
        overlap = _jaccard(tokens, other_tokens)
        if overlap > max_overlap:
            max_overlap = overlap

    # 完全不重叠=1.0, 完全重叠=0.0
    return 1.0 - max_overlap


# ═══════════════════════════════════════════════════════════════
# 核心评分
# ═══════════════════════════════════════════════════════════════


def compute_confidence_scores(
    requirements: list[dict[str, Any]],
    raw_text: str = "",
    covered_indices: Set[int] | None = None,
    calibration_data: dict[str, Any] | None = None,
) -> None:
    """
    对所有需求原地标注置信度字段。零 AI 调用。

    参数:
        requirements:     需求列表（原地修改，添加置信度字段）
        raw_text:         原始文档文本（用于 source_quote 验证）
        covered_indices:  已覆盖的段落索引集合（用于 section_covered 判断）
        calibration_data: 校准数据（可选，用于冷启动判断）

    产出字段（每条需求添加）:
        _evidence_confidence
        _confidence_level
        _evidence_verdict
        _ai_raw_confidence
        _evidence_calibration_samples
        _evidence_cold_start
    """
    if not requirements:
        return

    # 预计算所有需求的 tokens（用于 uniqueness）
    all_tokens = {}
    for req in requirements:
        desc = req.get("description", "")
        all_tokens[id(req)] = _extract_2gram_tokens(desc)

    # ★ S2-011: 按模块预计算 section_covered 比例
    # 规格 §3.2.3: section_covered = 该模块内的需求条目数 / 模块段落数
    # 模块段落数 = 该模块需求引用的唯一 §N 段落索引数
    _module_req_count: dict[str, int] = {}
    _module_para_indices: dict[str, Set[int]] = {}
    for req in requirements:
        mod = req.get("module", "") or "_default_"
        _module_req_count[mod] = _module_req_count.get(mod, 0) + 1
        if mod not in _module_para_indices:
            _module_para_indices[mod] = set()
        source = req.get("source", "") or ""
        for m in _re.finditer(r"§(\d+)", source):
            _module_para_indices[mod].add(int(m.group(1)))

    def _compute_section_covered(req: dict[str, Any]) -> float:
        """★ S2-011: 按模块比例计算 section_covered。"""
        mod = req.get("module", "") or "_default_"
        req_count = _module_req_count.get(mod, 0)
        para_indices = _module_para_indices.get(mod, set())
        para_count = len(para_indices)
        # 有段落引用时按比例计算
        if para_count > 0:
            ratio = req_count / para_count
            return min(ratio, 1.0)
        # 无 §N 标记降级：有 source 给 0.5，无 source 给 0.0
        source = req.get("source", "") or ""
        if source:
            # 进一步检查 covered_indices 兼容性
            if covered_indices is not None:
                for m in _re.finditer(r"§(\d+)", source):
                    if int(m.group(1)) in covered_indices:
                        return 1.0
            return 0.5
        return 0.0

    cal_samples = 0
    is_cold = True
    if calibration_data and isinstance(calibration_data, dict):
        cal_samples = calibration_data.get("total_samples", 0)
        if cal_samples >= 30:
            is_cold = False

    for req in requirements:
        # ── 维度1: source_traceable ──
        sq = req.get("source_quote", "").strip()
        has_source = 1.0 if sq else 0.0

        # ── 维度2: source_verified ──
        # ★ v2.1: 消费 Step⑤ source_verify 的 _verify_status，未命中给 0.0（不再兜底 0.3）
        # ★ 修复(07-05): 5B NLI 从未实现，删除 passed_nli 死分支；
        #   修复命名bug：5a 实际产出 "passed"（非 "passed_substring"），原代码永远不命中
        # ★ 修复(07-05b): 补充 partial_match 状态消费（5a 部分匹配给 0.7 分）
        verify_status = req.get("_verify_status", "")
        source_verified = 0.0
        if verify_status:
            # 优先消费三阶段校验结果
            if verify_status == "passed":
                source_verified = 1.0  # 5a 子串匹配通过
            elif verify_status in ("passed_corrected", "partial_match"):
                source_verified = 0.7  # 切片级修正后通过 / 5a 部分匹配
            elif verify_status in ("blocked", "no_quote"):
                source_verified = 0.0  # 核对未通过
        elif sq and raw_text:
            # 降级：无 _verify_status 时走旧前缀匹配
            if sq[:30] in raw_text:
                source_verified = 1.0
            elif sq[:20] in raw_text:
                source_verified = 0.7
            else:
                source_verified = 0.0  # ★ 未命中给 0.0（不再兜底 0.3）

        # ── 维度3: section_covered ──
        # ★ S2-011: 改为按模块比例计算（该模块需求条目数 / 模块段落数）
        section_covered = _compute_section_covered(req)

        # ── 维度4: uniqueness ──
        uniqueness = _compute_uniqueness(req, requirements)

        # ── 维度5: description_specific ──
        desc = req.get("description", "")
        desc_len = len(desc)
        if desc_len > 80:
            specificity = 1.0
        elif desc_len > 40:
            specificity = 0.7
        elif desc_len > 20:
            specificity = 0.5
        else:
            specificity = 0.3

        # ★ S2-010: encoding_penalty — 检测描述中的编码问题（乱码/替换字符）
        encoding_penalty = 0.0
        if desc:
            # 检测 Unicode 替换字符(U+FFFD)和常见 mojibake 模式
            if "\ufffd" in desc:
                encoding_penalty = 0.5
            elif any(ord(c) > 0xFFFF for c in desc):
                encoding_penalty = 0.05  # 四字节字符轻微惩罚
            # 检测连续的非 ASCII 乱码模式（如 GBK 误解码为 Latin-1）
            _garbled = sum(1 for c in desc if ord(c) > 127 and ord(c) < 256)
            if _garbled > len(desc) * 0.3:
                encoding_penalty = max(encoding_penalty, 0.3)

        # ── 加权计算 ──
        confidence = (
            _WEIGHT_SOURCE_TRACEABLE * has_source
            + _WEIGHT_SOURCE_VERIFIED * source_verified
            + _WEIGHT_SECTION_COVERED * section_covered
            + _WEIGHT_UNIQUENESS * uniqueness
            + _WEIGHT_SPECIFICITY * specificity
        ) - encoding_penalty  # ★ 编码惩罚扣减

        # ── 置信度档位 ──
        if confidence >= _HIGH_THRESHOLD:
            level = "HIGH"
            verdict = "verified"
        elif confidence >= _MEDIUM_THRESHOLD:
            level = "MEDIUM"
            verdict = "needs_review"
        else:
            level = "LOW"
            verdict = "uncertain"

        # ── AI 原始置信度（如果有，保留；没有则用结构评分估算）──
        ai_raw = req.get("_ai_raw_confidence")
        if ai_raw is None:
            ai_raw = round(confidence, 2)

        # ── 写入字段 ──
        req["_evidence_confidence"] = round(confidence, 3)
        req["_confidence_level"] = level
        req["_evidence_verdict"] = verdict
        req["_ai_raw_confidence"] = ai_raw
        req["_evidence_calibration_samples"] = cal_samples
        req["_evidence_cold_start"] = is_cold

    high_count = sum(1 for r in requirements if r.get("_confidence_level") == "HIGH")
    med_count = sum(1 for r in requirements if r.get("_confidence_level") == "MEDIUM")
    low_count = sum(1 for r in requirements if r.get("_confidence_level") == "LOW")
    _log.info(
        "[置信度评分] %d 条需求: HIGH=%d MEDIUM=%d LOW=%d (冷启动=%s)",
        len(requirements),
        high_count,
        med_count,
        low_count,
        is_cold,
    )
