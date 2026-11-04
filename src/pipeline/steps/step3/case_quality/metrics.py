# -*- coding: utf-8 -*-
"""用例驱动指标计算 (v3.0) — 从 checks[] + actions[] 读取，不依赖废弃的 verification_plan。"""

import json
from typing import Any
import logging

_log = logging.getLogger(__name__)


def _parse_checks(case: dict[str, Any]) -> list:
    checks = case.get("checks") or []
    if isinstance(checks, str):
        try:
            checks = json.loads(checks)
        except Exception as _e:
            _log.debug("[metrics] _parse_checks 异常: %s", _e, exc_info=True)
            checks = []
    return checks if isinstance(checks, list) else []


def _parse_actions(case: dict[str, Any]) -> list:
    actions = case.get("actions") or []
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except Exception as _e:
            _log.debug("[metrics] _parse_actions 异常: %s", _e, exc_info=True)
            actions = []
    return actions if isinstance(actions, list) else []


def compute_case_metrics(case: dict[str, Any]) -> dict[str, Any]:
    """v3.0: 计算单条测试用例的驱动指标（基于 checks[] + actions[]）。

    返回：
    {
        case_completeness:  float (0-100),  // 核心字段填充率
        evidence_total:       int,             // checks[] 总数
        ui_evidence:          int,             // UI 轨 check 数
        api_evidence:         int,             // API 轨 check 数
        db_evidence:          int,             // DB 轨 check 数
        avg_confidence:       float (0-1),     // 平均置信度（exact=0.95, fuzzy=0.7）
        case_driven_rate:   float (0-100),  // 综合用例驱动评分
        strictness_score:     float (0-100),  // 精确度评分（exact>fuzzy）
        action_count:         int,             // actions[] 操作数
    }
    """
    empty = {
        "case_completeness": 0.0,
        "evidence_total": 0,
        "ui_evidence": 0,
        "api_evidence": 0,
        "db_evidence": 0,
        "avg_confidence": 0.0,
        "case_driven_rate": 0.0,
        "strictness_score": 0.0,
        "action_count": 0,
    }
    if not case:
        return empty

    checks = _parse_checks(case)
    actions = _parse_actions(case)

    # 核心字段填充率：title + type + risk_level + has_actions + has_checks + has_expected
    # 防止仅靠3个必填字段就100%的假象
    CORE_FIELDS = ["title", "type", "risk_level"]
    filled = sum(1 for f in CORE_FIELDS if case.get(f))
    # 额外维度：actions[] 非空、checks[] 非空、expected_outcome 非空
    if len(actions) > 0:
        filled += 1
    if len(checks) > 0:
        filled += 1
    if case.get("expected_outcome") or case.get("expected"):
        filled += 1
    case_completeness = filled / 6 * 100.0

    # 从 checks[] 按 track 统计
    evidence_counts = {"ui": 0, "api": 0, "db": 0}
    confidence_sum = 0.0
    confidence_count = 0
    strictness_total = 0
    strict_count = 0

    for c in checks:
        if not isinstance(c, dict):
            continue
        track = str(c.get("track", "")).lower()
        if track in evidence_counts:
            evidence_counts[track] += 1

        # precision → confidence 映射
        precision = str(c.get("precision", "exact")).lower()
        conf = 0.95 if precision == "exact" else 0.7
        confidence_sum += conf
        confidence_count += 1

        # strictness: exact=high(3), fuzzy=medium(2)
        strictness_total += 3 if precision == "exact" else 2
        strict_count += 1

    evidence_total = sum(evidence_counts.values())
    avg_conf = round(confidence_sum / max(confidence_count, 1), 2)
    strictness_score = round(strictness_total / max(strict_count, 1) / 3 * 100, 1)

    # 综合评分：完整度 30% + 证据覆盖 30% + 置信度 15% + 严格性 10% + 可操作性 15%
    evidence_rate = min(evidence_total / 6 * 100, 100) if evidence_total else 0
    action_rate = min(len(actions) / 3 * 100, 100) if actions else 0

    weighted = (
        case_completeness * 0.30
        + evidence_rate * 0.30
        + (avg_conf * 100) * 0.15
        + strictness_score * 0.10
        + action_rate * 0.15
    )
    case_driven_rate = round(weighted, 1)
    case_coverage = round(evidence_rate, 1)

    return {
        "case_completeness": round(case_completeness, 1),
        "case_coverage": case_coverage,
        "evidence_total": evidence_total,
        "ui_evidence": evidence_counts.get("ui", 0),
        "api_evidence": evidence_counts.get("api", 0),
        "db_evidence": evidence_counts.get("db", 0),
        "avg_confidence": avg_conf,
        "strictness_score": strictness_score,
        "case_driven_rate": case_driven_rate,
        "action_count": len(actions),
    }
