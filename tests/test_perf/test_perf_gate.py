# -*- coding: utf-8 -*-
"""性能·门禁层(gate)测试：绝对阈值、相对退化、NO_DATA fail-closed、汇总。

覆盖判定优先级（短路）：error/空指标→NO_DATA；绝对阈值→FAIL；
无基线→BASELINE_ESTABLISHED；相对上浮≥fail→FAIL；∈[pre_gate,fail)→PRE_GATE；其余→PASS。
"""
from __future__ import annotations

import pytest

from src.extensions.perf_baseline.config import load_rules
from src.extensions.perf_baseline.gate import evaluate_gate, summarize
from src.extensions.perf_baseline.schemas import (
    GATE_BASELINE_ESTABLISHED,
    GATE_FAIL,
    GATE_NO_DATA,
    GATE_PASS,
    GATE_PRE_GATE,
    KIND_API,
    PerfSample,
)


def _sample(**kw) -> PerfSample:
    base = dict(
        kind=KIND_API,
        key="api::GET /items",
        target="api_demo",
        metrics={"elapsed_ms": 100.0, "status_code": 200},
    )
    base.update(kw)
    return PerfSample(**base)


@pytest.fixture()
def rules() -> dict:
    return load_rules("api_demo")


def _baseline_entry(mean: float) -> dict:
    return {"metrics": {"elapsed_ms": {"mean": mean}, "status_code": {"mean": 200}}}


# ── 优先级 1：采集异常 / 空指标 → NO_DATA（fail-closed，绝不 PASS）──────
def test_error_sample_no_data(rules):
    v = evaluate_gate(_sample(error="boom"), None, rules)
    assert v.status == GATE_NO_DATA


def test_empty_metrics_no_data(rules):
    v = evaluate_gate(_sample(metrics={}), None, rules)
    assert v.status == GATE_NO_DATA


def test_non_sample_and_empty_rules_no_data():
    rules = load_rules("api_demo")
    assert evaluate_gate("not-a-sample", None, rules).status == GATE_NO_DATA
    assert evaluate_gate(_sample(), None, {}).status == GATE_NO_DATA


def test_disabled_group_no_data(rules):
    """web 组在 api_demo 关停 → 即便有 web 样本也 NO_DATA。"""
    s = PerfSample(kind="web", key="web::/", target="api_demo", metrics={"load_ms": 100})
    assert evaluate_gate(s, None, rules).status == GATE_NO_DATA


# ── 优先级 2：绝对阈值超限 → FAIL ──────────────────────────────────────
def test_absolute_over_threshold_fail(rules):
    s = _sample(metrics={"elapsed_ms": 2000, "status_code": 200})  # 2000 > 800
    v = evaluate_gate(s, None, rules)
    assert v.status == GATE_FAIL
    assert v.violations and v.violations[0].rule_type == "absolute"


# ── 优先级 3：无基线 → BASELINE_ESTABLISHED ────────────────────────────
def test_no_baseline_established(rules):
    v = evaluate_gate(_sample(), None, rules)
    assert v.status == GATE_BASELINE_ESTABLISHED


# ── 优先级 4/5：相对基线上浮 → FAIL / PRE_GATE ─────────────────────────
def test_relative_above_fail_pct(rules):
    # 基线 100ms，当前 210ms → +110% ≥ 100% → FAIL
    v = evaluate_gate(_sample(metrics={"elapsed_ms": 210, "status_code": 200}),
                      _baseline_entry(100), rules)
    assert v.status == GATE_FAIL
    assert any(x.rule_type == "relative" for x in v.violations)


def test_relative_in_pre_gate_range(rules):
    # 基线 100ms，当前 185ms → +85% ∈ [80%, 100%) → PRE_GATE（不阻断）
    v = evaluate_gate(_sample(metrics={"elapsed_ms": 185, "status_code": 200}),
                      _baseline_entry(100), rules)
    assert v.status == GATE_PRE_GATE
    assert not v.is_blocking()


def test_relative_non_relative_metric_skipped(rules):
    """status_code 只做绝对，不做相对；基线波动不因它退化。"""
    v = evaluate_gate(_sample(metrics={"elapsed_ms": 100, "status_code": 500}),
                      _baseline_entry(100), rules)
    # 相对退化为 0；绝对阈值 status_code≥499 → FAIL（客观坏，仍应 FAIL）
    assert v.status == GATE_FAIL


def test_within_range_pass(rules):
    v = evaluate_gate(_sample(metrics={"elapsed_ms": 100, "status_code": 200}),
                      _baseline_entry(100), rules)
    assert v.status == GATE_PASS


# ── 噪声地板：与相对阈值同属**一条判据**（绝对增量不足即不构成退化）────
def _web_sample(ttfb: float) -> PerfSample:
    return PerfSample(kind="web", key="web::/products/1/edit", target="smartshop",
                      metrics={"ttfb_ms": ttfb, "dcl_ms": 200.0, "load_ms": 300.0})


def _web_baseline(ttfb: float) -> dict:
    return {"metrics": {
        "ttfb_ms": {"mean": ttfb},
        "dcl_ms": {"mean": 200.0},
        "load_ms": {"mean": 300.0},
    }}


def test_noise_floor_suppresses_low_absolute_high_percentage():
    """基线 14.57ms → 31.9ms：+118.94% 但绝对增量 17ms < 地板 50ms → 不判退化。"""
    rules = load_rules("smartshop")
    v = evaluate_gate(_web_sample(31.9), _web_baseline(14.57), rules)
    assert v.status == GATE_PASS
    assert v.violations == []


def test_noise_floor_suppresses_pre_gate_too():
    """+85% 落在 PRE_GATE 区间，但绝对增量不足地板 → 连告警都不发。"""
    rules = load_rules("smartshop")
    v = evaluate_gate(_web_sample(27.0), _web_baseline(14.57), rules)
    assert v.status == GATE_PASS


def test_true_regression_still_fails_with_floor():
    """真回归（绝对增量远超地板）仍必须 FAIL —— 地板不得掩盖退化。"""
    rules = load_rules("smartshop")
    v = evaluate_gate(_web_sample(400.0), _web_baseline(100.0), rules)
    assert v.status == GATE_FAIL
    assert any(x.rule_type == "relative" for x in v.violations)


def test_noise_floor_comes_from_structured_config_not_code():
    """地板值来自 ``relative.noise_floor`` 配置：置 0 → 同一数据恢复纯百分比判定。"""
    rules = load_rules("smartshop")
    rules["relative"]["noise_floor"] = {"ttfb_ms": 0}
    v = evaluate_gate(_web_sample(31.9), _web_baseline(14.57), rules)
    assert v.status == GATE_FAIL


# ── summarize：分布统计 + blocking ─────────────────────────────────────
def test_summarize_counts_and_blocking(rules):
    verdicts = [
        evaluate_gate(_sample(), None, rules),                          # BASELINE_ESTABLISHED
        evaluate_gate(_sample(metrics={"elapsed_ms": 2000}), None, rules),  # FAIL
        evaluate_gate(_sample(error="x"), None, rules),                 # NO_DATA
    ]
    s = summarize(verdicts)
    assert s["total"] == 3
    assert s["fail"] == 1 and s["baseline_established"] == 1 and s["no_data"] == 1
    assert s["blocking"] is True

    s2 = summarize([evaluate_gate(_sample(metrics={"elapsed_ms": 100}),
                                  _baseline_entry(100), rules)])        # PASS
    assert s2["pass"] == 1 and s2["blocking"] is False