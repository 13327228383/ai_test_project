# -*- coding: utf-8 -*-
"""性能基线与门禁（perf_gate）— 采集 → 暂存 → 基线 → 门禁 → 报告。

分层（单向依赖，无环）::

    collector  采集（Playwright / ApiTracker）
        ↓
    context    暂存（内存 + jsonl 兜底，run_id 隔离）
        ↓
    schemas    契约（PerfSample / MetricStat / Violation / GateVerdict）
        ↓
    baseline   基线存储（baselines/<target>.json，原子写）
        ↓
    gate       ★ 门禁判定唯一入口（纯函数）
        ↓
    report     结构化 JSON + Markdown 摘要

用法::

    from src.extensions.perf_baseline import (
        MetricCollector, perf_context, BaselineManager,
        evaluate_gate, summarize, load_rules,
        build_perf_report, render_perf_markdown, save_json,
    )

旧符号 ``PerfTracker`` / ``RegressionDetector`` / ``TrendAnalyzer`` 保留导出
（历史聚合与趋势分析仍可复用），``BaselineManager`` 已重写为按靶子分文件的
多指标版本，旧的 case 级接口以兼容层保留。

[已复核-DeepSeek-V3 2026-08-16]
"""

# ── 旧符号（向后兼容，历史聚合 / 趋势分析复用）──
from src.extensions.perf_baseline.tracker import PerfTracker
from src.extensions.perf_baseline.detector import RegressionDetector
from src.extensions.perf_baseline.trend import TrendAnalyzer

# ── 新链路符号 ──
from src.extensions.perf_baseline.schemas import (
    GATE_BASELINE_ESTABLISHED,
    GATE_FAIL,
    GATE_NO_DATA,
    GATE_PASS,
    GATE_PRE_GATE,
    GATE_STATUSES,
    KIND_API,
    KIND_WEB,
    PERCENTILE_ALGO,
    GateVerdict,
    MetricStat,
    PerfSample,
    Violation,
    normalize_key,
    percentile,
)
from src.extensions.perf_baseline.config import (
    DEFAULT_RULES_PATH,
    load_rules,
    metrics_of,
    relative_of,
    thresholds_of,
)
from src.extensions.perf_baseline.context import PerfContext, perf_context
from src.extensions.perf_baseline.collector import MetricCollector
from src.extensions.perf_baseline.baseline import BaselineManager
from src.extensions.perf_baseline.gate import evaluate_gate, summarize
from src.extensions.perf_baseline.report import (
    build_perf_report,
    render_perf_markdown,
    save_json,
)
from src.extensions.perf_baseline.regression import (
    compare,
    compare_with_history,
    load_history,
    render_regression_markdown,
    save_regression_report,
)

__all__ = [
    # 旧符号
    "PerfTracker",
    "RegressionDetector",
    "TrendAnalyzer",
    # 契约
    "PerfSample",
    "MetricStat",
    "Violation",
    "GateVerdict",
    "normalize_key",
    # 分位数唯一真源（整改 B2：step5/perf_baseline.py 已转发至此）
    "percentile",
    "PERCENTILE_ALGO",
    "KIND_WEB",
    "KIND_API",
    "GATE_PASS",
    "GATE_PRE_GATE",
    "GATE_FAIL",
    "GATE_NO_DATA",
    "GATE_BASELINE_ESTABLISHED",
    "GATE_STATUSES",
    # 配置
    "load_rules",
    "metrics_of",
    "thresholds_of",
    "relative_of",
    "DEFAULT_RULES_PATH",
    # 采集 / 暂存
    "MetricCollector",
    "PerfContext",
    "perf_context",
    # 基线 / 门禁 / 报告
    "BaselineManager",
    "evaluate_gate",
    "summarize",
    "build_perf_report",
    "render_perf_markdown",
    "save_json",
    # 回归对比
    "compare",
    "compare_with_history",
    "load_history",
    "render_regression_markdown",
    "save_regression_report",
]
