# -*- coding: utf-8 -*-
"""性能门禁判定 — ★ 全链路唯一判定入口（任何地方不得重复实现判定逻辑）。

**纯函数、无 IO、无全局状态**，因此可被单测 100% 覆盖。

判定优先级（§8.1，短路求值，顺序不可调整）::

    1. sample.error 非空 或 metrics 为空        → NO_DATA   (fail-closed)
    2. 任一指标 > 绝对阈值                       → FAIL
    3. baseline_entry is None                   → BASELINE_ESTABLISHED
    4. 任一指标相对基线 mean 上浮 ≥ fail_pct
       且绝对增量 ≥ 该指标噪声地板              → FAIL
    5. 任一指标上浮 ∈ [pre_gate_pct, fail_pct)
       且绝对增量 ≥ 该指标噪声地板              → PRE_GATE
    6. 其余                                     → PASS

补充约定：
  - ``delta_pct = (current - baseline_mean) / baseline_mean * 100``
  - ``baseline_mean <= 0`` 时跳过该指标的相对判定（除零保护）
  - **噪声地板与相对阈值是同一条判据的两个必要条件**（不是第二个判定入口）：
    低绝对值指标的百分比会被首访抖动放大（基线 ttfb 15ms → 32ms = +119%），
    此时绝对增量仅 17ms，不具备退化语义。地板值来自 ``relative.noise_floor``
    （结构化配置），未声明的指标地板为 0 → 纯百分比判定（历史行为）。
  - ``status_code`` / ``resource_count`` **只做绝对校验，不做相对**
  - ``blocking = (fail > 0)``；PRE_GATE 只告警不阻断
  - 遇非法入参返回 ``NO_DATA``（**绝不静默 PASS**）—— 这是本模块唯一的
    「异常表达方式」，符合 §8.6「对外函数不 raise」。

[已复核-DeepSeek-V3 2026-08-16]
"""

from __future__ import annotations

import logging
import math
from typing import Any

from src.extensions.perf_baseline.config import (
    metrics_of,
    noise_floor_of,
    relative_of,
    thresholds_of,
)
from src.extensions.perf_baseline.schemas import (
    GATE_BASELINE_ESTABLISHED,
    GATE_FAIL,
    GATE_NO_DATA,
    GATE_PASS,
    GATE_PRE_GATE,
    GATE_STATUSES,
    GateVerdict,
    KIND_API,
    KIND_WEB,
    PerfSample,
    Violation,
    _safe_float,
)

_log = logging.getLogger(__name__)

#: 不参与相对判定的指标（枚举型 / 计数型，波动无「退化」语义）
NON_RELATIVE_METRICS = frozenset({"status_code", "resource_count"})


def _no_data(sample: Any, reason: str) -> GateVerdict:
    """构造 NO_DATA 终态（fail-closed 的统一出口）。"""
    target = getattr(sample, "target", "") or ""
    key = getattr(sample, "key", "") or ""
    kind = getattr(sample, "kind", KIND_WEB) or KIND_WEB
    case_id = getattr(sample, "case_id", "") or ""
    metrics = getattr(sample, "metrics", None)
    return GateVerdict(
        status=GATE_NO_DATA,
        target=target,
        key=key,
        kind=kind,
        case_id=case_id,
        metrics=dict(metrics) if isinstance(metrics, dict) else {},
        violations=[],
        reasons=[reason],
    )


def _check_absolute(metric: str, current: float, threshold: float) -> Violation | None:
    """绝对阈值校验：``current > threshold`` 即 FAIL。"""
    if current <= threshold:
        return None
    return Violation(
        metric=metric,
        current=round(current, 2),
        rule_type="absolute",
        threshold=round(threshold, 2),
        baseline=0.0,
        delta_pct=0.0,
        level=GATE_FAIL,
        reason=f"{metric}={round(current, 2)} 超绝对阈值 {round(threshold, 2)}",
    )


def _check_relative(
    metric: str,
    current: float,
    baseline_mean: float,
    pre_gate_pct: float,
    fail_pct: float,
    noise_floor: float = 0.0,
) -> Violation | None:
    """相对基线上浮校验。基线均值 ≤ 0 时跳过（返回 ``None``）。

    ``noise_floor``：该指标的绝对噪声地板（与相对阈值同属一条判据）——
    绝对增量低于地板时不构成退化，直接返回 ``None``。
    """
    if baseline_mean <= 0:
        return None
    abs_delta = current - baseline_mean
    if noise_floor > 0 and abs_delta < noise_floor:
        return None
    delta_pct = round(abs_delta / baseline_mean * 100.0, 2)
    if delta_pct >= fail_pct:
        level, bound = GATE_FAIL, fail_pct
    elif delta_pct >= pre_gate_pct:
        level, bound = GATE_PRE_GATE, pre_gate_pct
    else:
        return None
    return Violation(
        metric=metric,
        current=round(current, 2),
        rule_type="relative",
        threshold=round(bound, 2),
        baseline=round(baseline_mean, 2),
        delta_pct=delta_pct,
        level=level,
        reason=f"{metric} 相对基线上浮 {delta_pct}% ≥ {round(bound, 2)}%"
        f"（绝对增量 {round(abs_delta, 2)} ≥ 噪声地板 {round(noise_floor, 2)}）",
    )


def _baseline_mean(baseline_entry: dict, metric: str) -> float:
    """从基线 entry 中取指定指标的 mean。缺失返回 ``0.0``（→ 跳过相对判定）。"""
    metrics = baseline_entry.get("metrics")
    if not isinstance(metrics, dict):
        return 0.0
    stat = metrics.get(metric)
    if not isinstance(stat, dict):
        return 0.0
    try:
        return float(stat.get("mean", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def evaluate_gate(
    sample: PerfSample,
    baseline_entry: dict | None,
    rules: dict,
) -> GateVerdict:
    """★ 门禁唯一入口：对单个样本产出终态判定。

    参数：
        sample: 采集样本。非 :class:`PerfSample` 时直接 ``NO_DATA``。
        baseline_entry: 该 key 的基线条目（``baselines/<target>.json`` 的
            ``entries[key]``）。``None`` 表示首次见到该 key。
        rules: :func:`config.load_rules` 的返回值。

    返回：
        :class:`GateVerdict`，``status`` ∈ :data:`schemas.GATE_STATUSES`。
        **永不抛异常**。
    """
    # ── 入参合法性（非法 → NO_DATA，绝不静默 PASS）──
    if not isinstance(sample, PerfSample):
        return _no_data(sample, "入参非 PerfSample（非法调用）")
    if not isinstance(rules, dict) or not rules:
        return _no_data(sample, "规则为空或非法，无法判定")

    kind = (sample.kind or KIND_WEB).strip().lower()
    if kind not in (KIND_WEB, KIND_API):
        return _no_data(sample, f"未知样本类型: {sample.kind}")

    # ── 优先级 1：采集失败 / 无指标 → NO_DATA（fail-closed）──
    if sample.error:
        return _no_data(sample, f"采集异常: {str(sample.error)[:160]}")
    if not sample.metrics:
        return _no_data(sample, "采集指标为空")

    configured = metrics_of(rules, kind)
    if not configured:
        return _no_data(sample, f"规则未启用 {kind} 指标组（该组已关停）")

    evaluated = [m for m in configured if m in sample.metrics]
    if not evaluated:
        return _no_data(sample, "采集指标与规则配置无交集")

    # ★ fail-closed 修复（BUG-1 / P1）：原裸 ``float()`` 遇脏值（``str`` /
    # ``None`` / ``dict`` / ``list`` / ``NaN`` / ``inf`` / 负数）会抛异常，
    # 被 ``run_perf_gate`` 外层 try 吞掉 → 整批判定塌成空报告 ``{}``，比误判
    # 更危险（同批真实 FAIL 也被静默）。这里宽容取值并剔除脏值，全部非法才
    # 回落 NO_DATA —— 符合模块 docstring「绝不静默 PASS / 永不抛异常」。
    current_metrics: dict[str, float] = {}
    for m in evaluated:
        num = _safe_float(sample.metrics[m], default=math.nan)
        if math.isnan(num) or num < 0:
            # 脏值（非数值 / None / NaN / inf / 负数）剔除，不参与判定；
            # 不静默吞、也不误判为「性能变好」。
            continue
        current_metrics[m] = num
    if not current_metrics:
        # 全部评估指标值均非法 → 整样本 NO_DATA（fail-closed，绝不 PASS）
        return _no_data(sample, "全部评估指标值非法（非数值 / NaN / inf / 负数），跳过判定")

    verdict = GateVerdict(
        status=GATE_PASS,
        target=sample.target,
        key=sample.key,
        kind=kind,
        case_id=sample.case_id,
        metrics={m: round(v, 2) for m, v in current_metrics.items()},
        violations=[],
        reasons=[],
    )

    # ── 优先级 2：绝对阈值（首跑也生效 —— 这是「客观坏」而非「相对退化」）──
    thresholds = thresholds_of(rules, kind)
    absolute_hits: list[Violation] = []
    for metric, current in current_metrics.items():
        threshold = thresholds.get(metric)
        if threshold is None:
            continue
        hit = _check_absolute(metric, current, threshold)
        if hit:
            absolute_hits.append(hit)
    if absolute_hits:
        verdict.status = GATE_FAIL
        verdict.violations = absolute_hits
        verdict.reasons = [v.reason for v in absolute_hits]
        return verdict

    # ── 优先级 3：无基线 → BASELINE_ESTABLISHED（不做相对判定）──
    if not isinstance(baseline_entry, dict) or not baseline_entry.get("metrics"):
        verdict.status = GATE_BASELINE_ESTABLISHED
        verdict.reasons = ["首次采集该 key，已建立基线（本轮不做相对判定）"]
        return verdict

    # ── 优先级 4/5：相对基线上浮（须同时越过绝对噪声地板）──
    pre_gate_pct, fail_pct = relative_of(rules)
    noise_floors = noise_floor_of(rules)
    relative_hits: list[Violation] = []
    for metric, current in current_metrics.items():
        if metric in NON_RELATIVE_METRICS:
            continue
        hit = _check_relative(
            metric,
            current,
            _baseline_mean(baseline_entry, metric),
            pre_gate_pct,
            fail_pct,
            noise_floors.get(metric, 0.0),
        )
        if hit:
            relative_hits.append(hit)

    if relative_hits:
        verdict.violations = relative_hits
        verdict.reasons = [v.reason for v in relative_hits]
        verdict.status = (
            GATE_FAIL
            if any(v.level == GATE_FAIL for v in relative_hits)
            else GATE_PRE_GATE
        )
        return verdict

    # ── 优先级 6：其余 → PASS ──
    verdict.status = GATE_PASS
    verdict.reasons = ["全部指标在基线与绝对阈值范围内"]
    return verdict


def summarize(verdicts: list[GateVerdict]) -> dict[str, Any]:
    """汇总判定结果。

    返回：
        ``{"total", "pass", "pre_gate", "fail", "baseline_established",
        "no_data", "blocking"}``。``blocking`` 仅由 FAIL 数量决定。
    """
    counters = {
        GATE_PASS: 0,
        GATE_PRE_GATE: 0,
        GATE_FAIL: 0,
        GATE_BASELINE_ESTABLISHED: 0,
        GATE_NO_DATA: 0,
    }
    total = 0
    for verdict in verdicts or []:
        status = getattr(verdict, "status", None)
        if status not in GATE_STATUSES:
            # 非法条目按 NO_DATA 计（fail-closed）
            counters[GATE_NO_DATA] += 1
            total += 1
            continue
        counters[status] += 1
        total += 1
    return {
        "total": total,
        "pass": counters[GATE_PASS],
        "pre_gate": counters[GATE_PRE_GATE],
        "fail": counters[GATE_FAIL],
        "baseline_established": counters[GATE_BASELINE_ESTABLISHED],
        "no_data": counters[GATE_NO_DATA],
        "blocking": counters[GATE_FAIL] > 0,
    }


__all__ = ["evaluate_gate", "summarize", "NON_RELATIVE_METRICS"]
