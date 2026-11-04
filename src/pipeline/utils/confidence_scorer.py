# -*- coding: utf-8 -*-
from __future__ import annotations

"""
AI 置信度评分器 (ConfidenceScorer)
==================================
为需求和测试用例计算统一的 0-100 置信度评分。

需求置信度：6 维加权合取（source_traceable + source_verified + section_covered + uniqueness + description_specific + completeness）
测试用例置信度：4 维锚点合取真分（source_traceable + source_verified + action_reachable + novelty），替旧基准 85 模型

评分仅存储在 MySQL case_meta 表中，不污染 Excel 字段。
"""

import json
from typing import Any
import logging

_log = logging.getLogger(__name__)


def _expected_specificity(expected: str, title: str) -> float:
    """期望文本具体性启发式（0-1），纯通用规则，不依赖业务词表。

    目的：把"…探索验证"这类自动填充的样板期望，与携带具体断言(状态码/数值/失败语)的
    真实期望区分开，从而拉开 navigate-only(API型) 用例之间的 source_verified 区分度。
    分层打分：
      - 空 / 与 title 几乎重复（自动填充样板）→ 低
      - 纯泛化探索措辞 → 中低
      - 含具体细节（数字/运算符/断言动词）→ 高
    """
    exp = (expected or "").strip()
    if not exp:
        return 0.0
    t = (title or "").strip()

    # 样板1：期望几乎等于标题（规则/Agent 自动回填），信息量≈0
    if t and (exp == t or exp.startswith(t[: min(len(t), 12)])):
        return 0.25
    # 样板2：纯泛化探索措辞（"…探索验证" / "…验证"），无具体断言
    if "探索" in exp or exp.endswith("验证") or exp.endswith("验证正常"):
        return 0.55
    # 具体断言细节：数字、比较/赋值符、明确的期望动词
    _has_detail = (
        any(ch.isdigit() for ch in exp)
        or any(op in exp for op in ("=>", "==", ">=", "<=", "!="))
        or "返回" in exp
        or "应为" in exp
        or "应当" in exp
        or "status" in exp.lower()
    )
    if _has_detail:
        return 1.0 if len(exp) >= 12 else 0.8
    if len(exp) >= 20:
        return 0.75
    if len(exp) >= 10:
        return 0.6
    return 0.5


def score_requirement(
    req: dict[str, Any], dep_validation: dict | None = None
) -> dict[str, Any]:
    """
    计算需求条目的置信度，返回完整6维置信度对象。

    返回格式: {"_avg_confidence": int, "source_traceable": float,
               "source_verified": float, "section_covered": float,
               "uniqueness": float, "description_specific": float,
               "completeness": float}

    六维权重（与前端 DIMENSIONS 一致）：
    - source_traceable (0.25): 原文可追溯
    - source_verified (0.20): 原文已验证
    - section_covered (0.15): 章节覆盖度
    - uniqueness (0.15): 唯一性
    - description_specific (0.10): 描述具体性
    - completeness (0.15): 完整性（★ 新增，基于 completeness_issues）
    """
    desc = req.get("description", "") or ""
    src = req.get("source", "")
    source_quote = req.get("source_quote", "")
    source_anchor = req.get("source_anchor", "")
    slice_id = req.get("slice_id", "")
    dependency_req_id = req.get("dependency_req_id", "")
    completeness_issues = req.get("completeness_issues", []) or []

    # 1. 原文可追溯 (0.25): 有 source_quote 或 source
    source_traceable = 1.0 if (source_quote or src) else 0.0

    # 2. 原文已验证 (0.20): 有 source_quote + source_anchor
    source_verified = (
        1.0 if (source_quote and source_anchor) else (0.6 if source_quote else 0.0)
    )

    # 3. 章节覆盖度 (0.15): 有 slice_id
    section_covered = 1.0 if slice_id else 0.0

    # 4. 唯一性 (0.15): 无依赖关系 = 更独立
    uniqueness = 0.3 if dependency_req_id else 1.0

    # 5. 描述具体性 (0.10): 描述长度
    desc_len = len(desc)
    if desc_len >= 60:
        description_specific = 1.0
    elif desc_len >= 30:
        description_specific = 0.6
    elif desc_len >= 10:
        description_specific = 0.3
    else:
        description_specific = 0.0

    # 6. 完整性 (0.15): 基于 completeness_issues 数量
    #    0 个问题 = 100%，每个问题扣 0.2，5 个以上 = 0%
    issue_count = len(completeness_issues)
    completeness = max(0.0, 1.0 - 0.2 * issue_count)

    # 加权平均
    weights = {
        "source_traceable": 0.25,
        "source_verified": 0.20,
        "section_covered": 0.15,
        "uniqueness": 0.15,
        "description_specific": 0.10,
        "completeness": 0.15,
    }
    dims = {
        "source_traceable": round(source_traceable, 2),
        "source_verified": round(source_verified, 2),
        "section_covered": round(section_covered, 2),
        "uniqueness": round(uniqueness, 2),
        "description_specific": round(description_specific, 2),
        "completeness": round(completeness, 2),
    }
    avg = sum(dims[k] * weights[k] for k in weights)
    dims["_avg_confidence"] = round(avg * 100)  # 0-100

    # 依赖交叉验证减分
    if dep_validation and isinstance(dep_validation, dict):
        if not dep_validation.get("valid", True):
            dims["_avg_confidence"] = max(0, dims["_avg_confidence"] - 15)
            dims["source_verified"] = max(0.0, dims["source_verified"] - 0.3)

    return dims


def score_test_case(
    intent: dict[str, Any],
    validation_warnings: list | None = None,
    cross_model_result: dict | None = None,
    all_intents: list[dict[str, Any]] | None = None,
) -> int:
    """
    计算测试用例的置信度（0-100）——锚点合取真分。

    ★ P0-3 3a: 删基准 85+扣分模型，改 4 维锚点合取真分。
      修复前: 基准 85 + 扣分（警告-5/个、title缺失-20、actions空-15）+ 加分
        → 用例大多无警告→不扣分→恒 85 → case_meta.confidence 192 条全=85.00
        → 与用例真实性零相关 → 假 PASS 温床
      修复后: 4 维锚点合取真分，每维 0-1 加权 ×100
        → confidence 与用例真实性正相关 → review_lane 可靠路由

    4 维（通用，不依赖业务术语）：
      - source_traceable (0.30): req_id 非空 → 可追溯到需求
      - source_verified  (0.25): title/expected 非空且具体 → 期望可验证
      - action_reachable (0.25): actions 含 navigate+click → 操作链完整可达
      - novelty          (0.20): title 与同 batch 不重复 → 非冗余

    交叉验证一致 → source_verified 加成（×1.2，上限 1.0）
    校验警告 → source_verified 扣减（每个 -0.1，下限 0.0）

    参数:
        all_intents: 同 batch 全部用例（用于 novelty 计算）；None 时 novelty=0.5（中性）
    """
    # ── 维度1: source_traceable (0.30) ──
    req_id = intent.get("req_id", "") or ""
    source_traceable = 1.0 if req_id else 0.0

    # ── 维度2: source_verified (0.25) ──
    #   通用性：期望(outcome)是否真实非空是"可验证"的最强信号，比描述长度更能拉开区分度。
    #   增强: 根因是"全 80% 分"——初版修复只要求 expected 非空，
    #   但大量用例的 expected 是"…探索验证"式自动填充样板，信息量极低，依旧全体同分。
    #   现增加"期望具体性 _expected_specificity"（样板识别 + 断言细节识别），配合
    #   checks 断言条数，让 API 型(navigate-only)用例之间也能按可验证性拉开区分度。
    title = intent.get("title", "") or ""
    # 期望文本以"真实作者断言"为准：优先聚合 checks[] 里每个断言的 expect 原文，
    # 其次 rule/Agent 回填的 expected_result，最后 expected_outcome/festback。
    # 根因：expected_outcome 常被自动回填成 "…探索验证" 样板（信息量≈0），
    # 而 checks[] 才承载具体断言（如"返回状态码 200"），反映真实可验证性。
    _checks = intent.get("checks") or []
    if isinstance(_checks, str):
        try:
            _checks = json.loads(_checks)
        except Exception as _e:
            _log.debug("[confidence_scorer] checks 解析异常: %s", _e)
            _checks = []
    _n_checks = len(_checks) if isinstance(_checks, list) else 0
    _check_texts = []
    if isinstance(_checks, list):
        for _c in _checks:
            if not isinstance(_c, dict):
                continue
            _v = (_c.get("expect") or _c.get("expected")
                  or _c.get("value") or _c.get("predicate") or "")
            if _v:
                _check_texts.append(str(_v).strip())
    expected = (
        " | ".join(t for t in _check_texts if t)
        or intent.get("expected_result") or ""
        or intent.get("expected_outcome", "") or intent.get("expected_feedback", "") or ""
    ).strip()
    _desc_len = len(title.strip()) + len(expected)

    if expected:
        _spec = _expected_specificity(expected, title)
        # 期望文本不再用"非空即满分"的粗粒度：具体程度 ×0.7 + 断言条数 ×0.3
        _depth = 0.7 * _spec + 0.3 * min(1.0, _n_checks / 3.0)
        source_verified = min(1.0, 0.40 + 0.60 * _depth)
    elif _desc_len >= 10:
        source_verified = 0.4  # 仅有 title，无 expected 期望 → 可验证性打折
    else:
        source_verified = 0.2

    # 交叉验证加成
    if cross_model_result and isinstance(cross_model_result, dict):
        if cross_model_result.get("confidence") == "high":
            source_verified = min(1.0, source_verified * 1.2)

    # 校验警告扣减
    if validation_warnings:
        source_verified = max(0.0, source_verified - 0.1 * len(validation_warnings))

    # ── 维度3: action_reachable (0.25) ──
    actions = intent.get("actions") or []
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except Exception as _e:
            _log.debug("[confidence_scorer] actions 解析异常: %s", _e, exc_info=True)
            actions = []

    if not actions:
        action_reachable = 0.0
    else:
        _has_navigate = any(
            isinstance(a, dict) and a.get("type") == "navigate" for a in actions
        )
        _has_interact = any(
            isinstance(a, dict) and a.get("type") in ("click", "fill", "select")
            for a in actions
        )
        if _has_navigate and _has_interact:
            action_reachable = 1.0
        elif _has_interact or _has_navigate:
            action_reachable = 0.6
        else:
            action_reachable = 0.3

    # ── 维度4: novelty (0.20) ──
    if all_intents and len(all_intents) > 1:
        _other_titles = set()
        for _other in all_intents:
            if _other is intent:
                continue
            _ot = (_other.get("title", "") or "").strip()
            if _ot:
                _other_titles.add(_ot)
        _cur_title = (title or "").strip()
        if _cur_title and _cur_title not in _other_titles:
            novelty = 1.0
        elif _cur_title:
            novelty = 0.3  # 重复
        else:
            novelty = 0.0
    else:
        novelty = 0.5  # 无 batch 上下文，中性

    # ── 加权合取 ──
    _weights = {
        "source_traceable": 0.30,
        "source_verified": 0.25,
        "action_reachable": 0.25,
        "novelty": 0.20,
    }
    _confidence = (
        _weights["source_traceable"] * source_traceable
        + _weights["source_verified"] * source_verified
        + _weights["action_reachable"] * action_reachable
        + _weights["novelty"] * novelty
    )

    return max(0, min(100, round(_confidence * 100)))


def confidence_color(score: int) -> str:
    """返回前端展示颜色。"""
    if score >= 80:
        return "#22c55e"  # 绿色
    elif score >= 60:
        return "#f59e0b"  # 黄色
    return "#ef4444"  # 红色


def get_avg_confidence(confidence_result):
    """从置信度结果提取平均分数（兼容旧格式）。"""
    if isinstance(confidence_result, int):
        return confidence_result
    if isinstance(confidence_result, dict):
        return confidence_result.get("_avg_confidence", 0)
    return 0
