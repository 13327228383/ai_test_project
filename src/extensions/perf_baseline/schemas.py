# -*- coding: utf-8 -*-
"""性能门禁契约层 — 数据结构 + key 归一化（唯一单点实现）。

本模块是 perf_gate 链路的「契约地基」：采集层（collector）产出 :class:`PerfSample`，
基线层（baseline）聚合 :class:`MetricStat`，门禁层（gate）产出 :class:`GateVerdict`
与 :class:`Violation`，报告层（report）只读渲染。

设计原则：
  - **纯数据 + 纯函数**：本模块不做任何 IO，不依赖 src.core / 数据库 / Playwright，
    因此可被单测零成本导入。
  - **不写死靶子**：``normalize_key`` 只认 kind/method/url，不认任何靶子名。
  - **对外函数不 raise**：异常内部 catch → warning → 返回安全默认值。

key 命名规范（§8.2，全局唯一）::

    web::/product/{id}
    api::GET /api/orders/{id}

[已复核-DeepSeek-V3 2026-08-16]
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

# 整改 B3.1：`_safe_float` 收敛到 src/core/utils/num.py 单一真源。
# num.py 是依赖图叶子（仅用标准库），不破坏本模块「可零成本单测导入」的设计原则。
from src.core.utils.num import safe_float as _safe_float

_log = logging.getLogger(__name__)

#: 分位数算法版本标识（整改 B2）。
#: ``step5/perf_baseline.py`` 原用朴素取整（``int(len*pct)`` 索引），现全仓统一为
#: 本模块 :func:`percentile` 的**线性插值**。二者对同一组样本会给出不同的 P95/P99，
#: 故所有产出分位数的报告/基线都必须携带本标识，避免与历史数据被误当作同口径比较。
PERCENTILE_ALGO = "linear_interp_v2"

# ── 门禁终态常量（gate.py 唯一产出，其他模块只读引用，禁止硬编码字符串）──
GATE_PASS = "PASS"
GATE_PRE_GATE = "PRE_GATE"
GATE_FAIL = "FAIL"
GATE_NO_DATA = "NO_DATA"
GATE_BASELINE_ESTABLISHED = "BASELINE_ESTABLISHED"

#: 所有合法终态（报告层用于稳定排序 / 分布统计）
GATE_STATUSES = (
    GATE_PASS,
    GATE_PRE_GATE,
    GATE_FAIL,
    GATE_BASELINE_ESTABLISHED,
    GATE_NO_DATA,
)

#: 样本类型
KIND_WEB = "web"
KIND_API = "api"

# ── URL 段归一化正则（纯数字 / UUID / 32 位 hex → "{id}"）──
_RE_DIGITS = re.compile(r"^\d+$")
_RE_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_RE_HEX32 = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)

#: 每个指标在基线中保留的滚动样本窗口上限（用于精确重算 p50/p95）
METRIC_WINDOW = 200


def now_ts() -> str:
    """统一时间戳格式（与 ``_bug_report.py`` 保持一致）。"""
    return datetime.now().isoformat(timespec="seconds")


def normalize_path(url: str) -> str:
    """URL → 可跨环境比对的归一化 path。

    规则（§8.2）：去 query/fragment → 丢弃 scheme+host → 去尾斜杠（根路径保留 ``/``）
    → 段级替换（纯数字 / UUID / 32 位 hex → ``{id}``）。

    参数：
        url: 原始 URL，可为绝对地址或相对路径，允许为空。

    返回：
        归一化 path，永不为空（最差返回 ``"/"``）。
    """
    raw = (url or "").strip()
    if not raw:
        return "/"
    try:
        path = urlsplit(raw).path or "/"
    except Exception as exc:  # noqa: BLE001 — 对外不 raise
        _log.warning("[perf_gate] URL 解析失败(%s)，回落原串: %s", str(exc)[:80], raw[:80])
        path = raw.split("?", 1)[0].split("#", 1)[0] or "/"

    if not path.startswith("/"):
        path = "/" + path
    # 去尾斜杠，但根路径保留
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    segments = path.split("/")
    normalized = [
        "{id}"
        if seg and (_RE_DIGITS.match(seg) or _RE_UUID.match(seg) or _RE_HEX32.match(seg))
        else seg
        for seg in segments
    ]
    result = "/".join(normalized)
    return result or "/"


def normalize_key(kind: str, method: str, url: str) -> str:
    """产出全局唯一的指标 key（**唯一单点实现，任何地方不得另写一份**）。

    参数：
        kind: ``"web"`` 或 ``"api"``，其他值一律按 web 处理。
        method: HTTP 方法（仅 api 生效），空则按 ``GET``。
        url: 原始 URL。

    返回：
        ``"web::/product/{id}"`` 或 ``"api::GET /api/orders/{id}"``。
    """
    kind_norm = (kind or "").strip().lower() or KIND_WEB
    path = normalize_path(url)
    if kind_norm == KIND_API:
        verb = (method or "").strip().upper() or "GET"
        return f"{KIND_API}::{verb} {path}"
    return f"{KIND_WEB}::{path}"


@dataclass
class PerfSample:
    """一次采集产出的性能样本（采集层 → 暂存 → 门禁层的唯一载体）。

    ``error`` 非空表示采集失败：门禁层据此判定 ``NO_DATA``（fail-closed），
    **绝不冒充 PASS**。
    """

    kind: str = KIND_WEB
    key: str = ""
    target: str = ""
    run_id: str = ""
    case_id: str = ""
    raw_url: str = ""
    metrics: dict[str, float] = field(default_factory=dict)
    collected_at: str = field(default_factory=now_ts)
    error: str = ""

    def is_valid(self) -> bool:
        """是否为可参与门禁判定的有效样本。"""
        return (not self.error) and bool(self.metrics)

    def to_dict(self) -> dict[str, Any]:
        """序列化（用于 jsonl 落盘与报告渲染）。"""
        return {
            "kind": self.kind,
            "key": self.key,
            "target": self.target,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "raw_url": self.raw_url,
            "metrics": dict(self.metrics or {}),
            "collected_at": self.collected_at,
            "error": self.error,
        }

    @staticmethod
    def from_dict(d: Any) -> "PerfSample":
        """反序列化。入参非法时返回带 error 的空样本（不 raise）。"""
        if not isinstance(d, dict):
            return PerfSample(error="from_dict: 入参非 dict")
        raw_metrics = d.get("metrics") or {}
        metrics: dict[str, float] = {}
        if isinstance(raw_metrics, dict):
            for name, value in raw_metrics.items():
                if value is None:
                    continue
                metrics[str(name)] = _safe_float(value)
        return PerfSample(
            kind=str(d.get("kind") or KIND_WEB),
            key=str(d.get("key") or ""),
            target=str(d.get("target") or ""),
            run_id=str(d.get("run_id") or ""),
            case_id=str(d.get("case_id") or ""),
            raw_url=str(d.get("raw_url") or ""),
            metrics=metrics,
            collected_at=str(d.get("collected_at") or now_ts()),
            error=str(d.get("error") or ""),
        )


@dataclass
class MetricStat:
    """单指标的基线统计量（mean/p50/p95/min/max + 滚动样本窗口）。

    ``values`` 是实现细节（§8.3 schema 的超集）：保留最近 ``METRIC_WINDOW`` 个
    观测值，使 p50/p95 每次都能被**精确重算**而非增量近似。
    """

    mean: float = 0.0
    p50: float = 0.0
    p95: float = 0.0
    min: float = 0.0
    max: float = 0.0
    samples: int = 0
    values: list[float] = field(default_factory=list)

    def merge(self, value: Any) -> "MetricStat":
        """并入一个新观测值并就地重算全部统计量，返回自身（便于链式调用）。"""
        num = _safe_float(value, default=math.nan)
        if math.isnan(num):
            return self
        self.values.append(num)
        if len(self.values) > METRIC_WINDOW:
            self.values = self.values[-METRIC_WINDOW:]
        self._recompute()
        return self

    def _recompute(self) -> None:
        """基于当前窗口重算 mean/p50/p95/min/max/samples。"""
        vals = self.values
        if not vals:
            self.mean = self.p50 = self.p95 = self.min = self.max = 0.0
            self.samples = 0
            return
        self.samples = len(vals)
        self.mean = round(sum(vals) / len(vals), 2)
        self.min = round(min(vals), 2)
        self.max = round(max(vals), 2)
        self.p50 = percentile(vals, 50.0)
        self.p95 = percentile(vals, 95.0)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 §8.3 基线 schema 的 metrics 叶子节点。"""
        return {
            "mean": self.mean,
            "p50": self.p50,
            "p95": self.p95,
            "min": self.min,
            "max": self.max,
            "samples": self.samples,
            "values": [round(v, 2) for v in self.values],
        }

    @staticmethod
    def from_dict(d: Any) -> "MetricStat":
        """反序列化。缺 ``values`` 时用 mean 重建单点窗口（向后兼容旧文件）。"""
        if not isinstance(d, dict):
            return MetricStat()
        raw_values = d.get("values")
        values: list[float] = []
        if isinstance(raw_values, list):
            values = [_safe_float(v) for v in raw_values if v is not None]
        stat = MetricStat(
            mean=_safe_float(d.get("mean")),
            p50=_safe_float(d.get("p50")),
            p95=_safe_float(d.get("p95")),
            min=_safe_float(d.get("min")),
            max=_safe_float(d.get("max")),
            samples=int(_safe_float(d.get("samples"))),
            values=values,
        )
        if not values and stat.mean > 0:
            stat.values = [stat.mean]
        return stat


@dataclass
class Violation:
    """一条门禁违规明细（绝对阈值超限或相对基线上浮超限）。"""

    metric: str = ""
    current: float = 0.0
    rule_type: str = "absolute"  # "absolute" | "relative"
    threshold: float = 0.0
    baseline: float = 0.0
    delta_pct: float = 0.0
    level: str = GATE_FAIL  # "FAIL" | "PRE_GATE"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为 §8.5 报告 schema 的 violations 元素。"""
        return {
            "metric": self.metric,
            "current": self.current,
            "rule_type": self.rule_type,
            "threshold": self.threshold,
            "baseline": self.baseline,
            "delta_pct": self.delta_pct,
            "level": self.level,
            "reason": self.reason,
        }


@dataclass
class GateVerdict:
    """单个 key 的门禁判定结果（``gate.evaluate_gate`` 唯一产出）。"""

    status: str = GATE_NO_DATA
    target: str = ""
    key: str = ""
    kind: str = KIND_WEB
    case_id: str = ""
    metrics: dict[str, float] = field(default_factory=dict)
    violations: list[Violation] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def is_blocking(self) -> bool:
        """是否阻断（仅 FAIL 阻断；PRE_GATE 只告警，NO_DATA 只暴露）。"""
        return self.status == GATE_FAIL

    def to_dict(self) -> dict[str, Any]:
        """序列化为 §8.5 报告 schema 的 items 元素。"""
        return {
            "key": self.key,
            "kind": self.kind,
            "status": self.status,
            "target": self.target,
            "case_id": self.case_id,
            "metrics": dict(self.metrics or {}),
            "violations": [v.to_dict() for v in self.violations],
            "reasons": list(self.reasons),
        }


def percentile(values: list[float], pct: float) -> float:
    """线性插值分位数（标准 numpy 兼容算法，零第三方依赖）。

    参数：
        values: 观测值列表，允许为空。
        pct: 分位点（0~100）。

    返回：
        分位值（保留两位小数）；空列表返回 ``0.0``。
    """
    if not values:
        return 0.0
    ordered = sorted(_safe_float(v) for v in values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = (len(ordered) - 1) * max(0.0, min(100.0, pct)) / 100.0
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return round(ordered[int(rank)], 2)
    interpolated = ordered[low] + (ordered[high] - ordered[low]) * (rank - low)
    return round(interpolated, 2)


__all__ = [
    "GATE_PASS",
    "GATE_PRE_GATE",
    "GATE_FAIL",
    "GATE_NO_DATA",
    "GATE_BASELINE_ESTABLISHED",
    "GATE_STATUSES",
    "KIND_WEB",
    "KIND_API",
    "METRIC_WINDOW",
    "PERCENTILE_ALGO",
    "PerfSample",
    "MetricStat",
    "Violation",
    "GateVerdict",
    "normalize_key",
    "normalize_path",
    "percentile",
    "now_ts",
]
