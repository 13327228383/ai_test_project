# -*- coding: utf-8 -*-
"""
置信度汇总
"""
from typing import Any
import logging

_log = logging.getLogger(__name__)


def _collect_confidence_summary(
    intents: list[dict[str, Any]],
    confidence_results: list[dict[str, Any]] = None,
    requirements: list[dict[str, Any]] = None,
) -> dict[str, Any]:
    conf_map = {}
    for item in confidence_results or []:
        if isinstance(item, dict) and item.get("case_id"):
            conf_map[str(item.get("case_id"))] = item
    vals = []
    ev_vals = []  # ★ v7: 证据引擎校准后的置信度
    ev_samples = []  # ★ M3: 校准样本数
    ai_raw_vals = []  # ★ L1: AI 原始声称置信度（用于对比）
    factor_sums = {}
    factor_count = 0
    low_count = 0
    ev_cold_count = 0  # ★ v7: 冷启动计数
    verdict_counts = {}  # ★ L3: 各 verdict 类型计数
    for c in intents or []:
        ci = conf_map.get(str(c.get("case_id", "")))
        if ci is None and isinstance(c.get("_confidence"), dict):
            ci = c.get("_confidence")
        if not isinstance(ci, dict):
            continue
        try:
            val = float(ci.get("confidence", 0) or 0)
        except Exception as _e:
            _log.debug(
                "[_summary] _collect_confidence_summary 异常: %s", _e, exc_info=True
            )
            val = 0.0
        if val > 0:
            vals.append(val)
            if val < 0.7:
                low_count += 1
        # ★ v7: 收集证据引擎校准置信度
        ev_conf = c.get("_evidence_confidence")
        if ev_conf is not None:
            try:
                ev_vals.append(float(ev_conf))
            except Exception as _e:
                _log.debug("[_summary] 非关键异常: %s", _e, exc_info=True)
        # ★ M2: 直接读 bool 字段，不再字符串匹配 verdict
        if c.get("_evidence_cold_start"):
            ev_cold_count += 1
        # ★ L3: 统计各 verdict 类型
        _verdict = str(c.get("_evidence_verdict", "") or "").strip()
        if _verdict:
            verdict_counts[_verdict] = verdict_counts.get(_verdict, 0) + 1
        # ★ L1: 收集 AI 原始声称置信度
        _ai_raw = c.get("_ai_raw_confidence")
        if _ai_raw is not None:
            try:
                ai_raw_vals.append(float(_ai_raw))
            except Exception as _e:
                _log.debug("[_summary] 非关键异常: %s", _e, exc_info=True)
        # ★ M3: 收集校准样本数
        _ev_cs = c.get("_evidence_calibration_samples", 0)
        if _ev_cs:
            try:
                ev_samples.append(int(_ev_cs))
            except Exception as _e:
                _log.debug("[_summary] 非关键异常: %s", _e, exc_info=True)
        factors = ci.get("factors") or {}
        if isinstance(factors, dict) and factors:
            for k, v in factors.items():
                try:
                    factor_sums[k] = factor_sums.get(k, 0.0) + float(v or 0)
                except Exception as _e:
                    _log.debug("[_summary] 非关键异常: %s", _e, exc_info=True)
            factor_count += 1
    factor_avg = (
        {k: round(v / factor_count, 4) for k, v in factor_sums.items()}
        if factor_count
        else {}
    )
    ev_avg = round(sum(ev_vals) / len(ev_vals), 4) if ev_vals else None
    # ★ BUGFIX MED#3: intent 证据置信度为空时，降级到需求级证据置信度
    if not ev_vals and requirements:
        _req_ev_vals = []
        for r in requirements:
            _ev_conf = r.get("_evidence_confidence")
            if _ev_conf is not None:
                try:
                    _req_ev_vals.append(float(_ev_conf))
                except Exception as _e:
                    _log.debug("[_summary] 非关键异常: %s", _e, exc_info=True)
        if _req_ev_vals:
            ev_vals = _req_ev_vals
            ev_avg = round(sum(ev_vals) / len(ev_vals), 4)
    # ★ H1+H2: 真实证据覆盖率 = 有独立验证的需求数 / 总需求数
    if requirements:
        _reqs_with_ev = sum(
            1 for r in requirements if r.get("_has_independent_evidence")
        )
        evidence_coverage = round(_reqs_with_ev / max(len(requirements), 1), 4)
    else:
        # 降级：无法获取需求数据时，尝试从意图 verdict 估算
        _non_ai = sum(
            1
            for c in intents or []
            if str(c.get("_evidence_verdict", "") or "")
            in ("evidence_calibrated", "calibrated_only")
        )
        evidence_coverage = round(_non_ai / max(len(intents or []), 1), 4)
    return {
        "avg_confidence": round(sum(vals) / len(vals), 4) if vals else 0.0,
        "low_confidence_count": low_count,
        "factor_avg": factor_avg,
        "op_cache_hit_rate": float(factor_avg.get("op_cache_hit_rate", 0.0) or 0.0),
        "capability_hit_rate": float(factor_avg.get("capability_hit_rate", 0.0) or 0.0),
        # ★ v7: 证据引擎指标
        "evidence_calibrated_confidence": ev_avg,
        "evidence_ai_raw_confidence": (
            round(sum(ai_raw_vals) / len(ai_raw_vals), 4) if ai_raw_vals else None
        ),  # ★ L1
        "evidence_coverage": evidence_coverage,  # ★ H1+H2: 真实独立验证覆盖率
        "evidence_cold_start_count": ev_cold_count,
        "evidence_calibration_samples": (
            max(ev_samples) if ev_samples else 0
        ),  # ★ BUGFIX LOW#8: max 非累计值，为"最多维度样本数"（保守估计）
        "evidence_verdict_distribution": verdict_counts,  # ★ L3
    }
