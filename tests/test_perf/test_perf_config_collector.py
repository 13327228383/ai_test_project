# -*- coding: utf-8 -*-
"""性能三层体系(采集→门禁→集成)·配置合并 + 采集数据契约测试。

覆盖：config 深合并不写死靶子、collector 指标清洗、key 归一化、分位数算法。
"""
from __future__ import annotations

import pytest

from src.extensions.perf_baseline.config import (
    load_rules,
    metrics_of,
    relative_of,
    thresholds_of,
)
from src.extensions.perf_baseline.schemas import (
    KIND_API,
    KIND_WEB,
    PerfSample,
    MetricStat,
    normalize_key,
    normalize_path,
    percentile,
)


# ── config：规则深合并（不改 Python 即配置新靶子）───────────────────────
def test_load_rules_defaults_present():
    rules = load_rules("__no_such_target__")
    assert KIND_WEB in rules and KIND_API in rules and "relative" in rules
    assert rules["target"] == "__no_such_target__"


def test_load_rules_api_demo_disables_web_group():
    """api_demo 在 perf_rules.yaml 里 web.metrics=[] → 整组关停。"""
    rules = load_rules("api_demo")
    assert metrics_of(rules, KIND_WEB) == []
    assert "elapsed_ms" in metrics_of(rules, KIND_API)


def test_load_rules_target_threshold_override_deep_merge():
    """target 专属阈值覆盖 defaults（叶子值以 target 为准）。"""
    default = load_rules("")
    api_default = thresholds_of(default, KIND_API).get("elapsed_ms")
    overriden = load_rules("api_demo")
    assert thresholds_of(overriden, KIND_API).get("elapsed_ms") != api_default


def test_load_rules_invalid_relative_swapped():
    """relative pre_gate>fail 配置非法 → 自动对调。"""
    import copy
    from src.extensions.perf_baseline import config as mod
    d = load_rules("").copy()
    # 直接构造非法 relative 经 _sanitize
    raw = copy.deepcopy(load_rules(""))
    raw["relative"] = {"pre_gate_pct": 90, "fail_pct": 60}
    out = mod._sanitize(raw)
    assert relative_of(out) == (60.0, 90.0)


# ── collector：指标清洗（剔除脏值，保留合法 0）─────────────────────────
def test_sanitize_metrics_drops_dirty_values():
    from src.extensions.perf_baseline.collector import _sanitize_metrics
    raw = {
        "elapsed_ms": 123.4,
        "status_code": 200,
        "bad_str": "abc",           # 非数值 → 剔除
        "none": None,               # None → 剔除
        "nan": float("nan"),
        "neg": -5,                  # 负数 → 剔除
        "resource_count": 0,        # 法律 0 → 保留
    }
    out = _sanitize_metrics(raw)
    assert out["elapsed_ms"] == 123.4
    assert out["status_code"] == 200
    assert out["resource_count"] == 0
    for bad in ("bad_str", "none", "nan", "neg"):
        assert bad not in out
    assert _sanitize_metrics(None) == {}


# ── schemas：key 归一化 + 分位数（纯函数）──────────────────────────────
def test_normalize_path_segments_and_trailing_slash():
    assert normalize_path("https://x.com/") == "/"
    assert normalize_path("https://x.com/product/12") == "/product/{id}"
    uuid = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
    assert normalize_path(f"/api/orders/{uuid}") == "/api/orders/{id}"   # UUID → {id}
    assert normalize_path("/p/1?ref=1#top") == "/p/{id}"


def test_normalize_key_formats():
    assert normalize_key(KIND_API, "get", "/api/orders/42") == "api::GET /api/orders/{id}"
    assert normalize_key(KIND_API, None, "/x") == "api::GET /x"
    assert normalize_key(KIND_WEB, "POST", "/") == "web::/"


def test_percentile_linear_interp():
    import math
    assert percentile([], 95) == 0.0
    assert percentile([5], 50) == 5.0
    # 线性插值：标准算法
    vals = [1.0, 2.0, 3.0, 4.0]
    # rank=(4-1)*50/100=1.5 → 2 + (3-2)*0.5 = 2.5
    assert percentile(vals, 50) == pytest.approx(2.5)


def test_perf_sample_roundtrip_and_invalid():
    s = PerfSample(kind=KIND_API, key="k", target="t", metrics={"elapsed_ms": 12.0})
    d = s.to_dict()
    r = PerfSample.from_dict(d)
    assert r.target == "t" and r.metrics["elapsed_ms"] == 12.0
    bad = PerfSample.from_dict("not-a-dict")
    assert bad.error != "" and not bad.is_valid()


def test_metric_stat_merge_and_window_cap():
    from src.extensions.perf_baseline.schemas import METRIC_WINDOW
    st = MetricStat()
    for i in range(1, 11):
        st.merge(i)
    assert st.samples == 10
    assert st.mean == pytest.approx(5.5)
    assert st.max == 10.0
    # 窗口上限：超限只保留最近 METRIC_WINDOW 条
    st2 = MetricStat()
    for i in range(METRIC_WINDOW + 20):
        st2.merge(i)
    assert len(st2.values) == METRIC_WINDOW