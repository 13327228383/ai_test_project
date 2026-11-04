# -*- coding: utf-8 -*-
"""单元测试：Step5 性能跨运行趋势区块渲染（render_perf_trend_section）。

Corresponds to 横向·性能三层体系 → step5 报告集成（跨运行退化预警可视化）。
覆盖：
  - 空 / no_history / 无用例 → 不渲染（空串）；
  - 有量化数据 → 汇总表 + 退化/改善用例明细表；
  - 仅 data_points<2 的量（无 ratio）→ 只入汇总、不混入明细。
"""

import pytest

from src.extensions.perf_baseline.trend import TrendAnalyzer
from src.pipeline.steps.step5._report_sections import (
    render_perf_trend_section,
    render_perf_trend_svg,
)


def test_empty_trend_renders_nothing():
    assert render_perf_trend_section({}) == ""
    assert render_perf_trend_section(None) == ""


def test_no_history_renders_nothing():
    assert render_perf_trend_section({"error": "no_history"}) == ""


def test_no_cases_renders_nothing():
    assert render_perf_trend_section({"total_cases": 0}) == ""


def test_renders_summary_and_detail():
    trend = {
        "total_runs": 3,
        "total_cases": 2,
        "trends": {
            "degrading": ["CASE_A"],
            "improving": ["CASE_B"],
            "stable": [],
            "insufficient_data": ["CASE_C"],
        },
        "details": {
            "CASE_A": {
                "case_id": "CASE_A", "trend": "degrading", "data_points": 5,
                "recent_avg_ms": 120.0, "earliest_avg_ms": 60.0, "ratio": 2.0,
            },
            "CASE_B": {
                "case_id": "CASE_B", "trend": "improving", "data_points": 4,
                "recent_avg_ms": 30.0, "earliest_avg_ms": 90.0, "ratio": 0.33,
            },
            # 数据不足（仅 1 个采样）→ 不应出现在明细表
            "CASE_C": {"case_id": "CASE_C", "trend": "insufficient_data",
                       "data_points": 1},
        },
    }
    md = render_perf_trend_section(trend)
    assert "性能跨运行趋势" in md
    assert "CASE_A" in md and "CASE_B" in md
    assert "持续变慢 (退化)" in md
    assert "已量化的退化/改善用例" in md  # 明细表存在
    # 明细行应包含退化用例的量化指标
    assert "CASE_A" in md.split("### 已量化的退化/改善用例")[1]


def test_insufficient_only_omits_detail_table():
    trend = {
        "total_runs": 2,
        "total_cases": 1,
        "trends": {
            "degrading": [], "improving": [], "stable": [],
            "insufficient_data": ["CASE_C"],
        },
        "details": {"CASE_C": {"trend": "insufficient_data", "data_points": 1}},
    }
    md = render_perf_trend_section(trend)
    assert "性能跨运行趋势" in md
    assert "### 已量化的退化/改善用例" not in md  # 无可量化明细 → 不渲染明细表


# ── 可视化升级：SVG 折线图 ──────────────────────────────────────
def test_svg_empty_returns_empty():
    assert render_perf_trend_svg({}) == ""
    assert render_perf_trend_svg(None) == ""


def test_svg_ignores_single_point_cases():
    # 仅单采样点（无法构成线）→ 不绘制
    assert render_perf_trend_svg({"CASE_A": [{"elapsed_ms": 100.0}]}) == ""


def test_svg_emits_valid_svg_markup():
    series = {
        "CASE_A": [
            {"elapsed_ms": 60.0}, {"elapsed_ms": 90.0}, {"elapsed_ms": 120.0},
        ],
        "CASE_B": [
            {"elapsed_ms": 90.0}, {"elapsed_ms": 50.0}, {"elapsed_ms": 30.0},
        ],
    }
    svg = render_perf_trend_svg(series)
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "<polyline" in svg
    assert "<circle" in svg
    assert "CASE_A" in svg and "CASE_B" in svg  # 图例含用例名
    # 单行（可被 _html 包装器原样嵌入 <p>，不跨行拆断 XML）
    assert "\n" not in svg


def test_svg_bounds_ratio_bars():
    # 至少 6 条用例时只绘前 6（限制图上密度）
    series = {
        "C%d" % i: [
            {"elapsed_ms": 10.0 * i + 5.0},
            {"elapsed_ms": 20.0 * i + 5.0},
        ]
        for i in range(8)
    }
    svg = render_perf_trend_svg(series)
    # 只产生前 6 条折线
    assert svg.count("<polyline") == 6


def test_analyze_all_exposes_positive_series(tmp_path):
    # 序列应为正序（早期 → 近期）。伪造 history 文件（文件名倒序=最新在前）
    perf_dir = tmp_path / "perf_baseline"
    perf_dir.mkdir()
    files = {
        "perf_3.json": {
            "run_id": "r3",
            "records": [{"case_id": "C1", "elapsed_ms": 120.0}],
        },
        "perf_2.json": {
            "run_id": "r2",
            "records": [{"case_id": "C1", "elapsed_ms": 90.0}],
        },
        "perf_1.json": {
            "run_id": "r1",
            "records": [{"case_id": "C1", "elapsed_ms": 60.0}],
        },
    }
    for name, data in files.items():
        (perf_dir / name).write_text(
            __import__("json").dumps(data), encoding="utf-8"
        )

    ana = TrendAnalyzer(storage_dir=str(perf_dir))
    out = ana.analyze_all()
    assert "series" in out
    assert out["total_runs"] == 3
    seq = out["series"]["C1"]
    # 正序：60 → 120（早期 → 近期，供 SVG 折线 x 轴推进）
    assert [p["elapsed_ms"] for p in seq] == [60.0, 90.0, 120.0]
    assert out["series"].keys() == {"C1"}