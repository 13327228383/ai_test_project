# -*- coding: utf-8 -*-
"""性能基线（W3 T05，L0-7）—— ApiTracker elapsed_ms 采集 + 跨 run 对比（零 DDL）。

职责：
  - collect_perf_metrics：把 {page_load_ms, api: {url: elapsed_ms}} 写入
    oracle_results.detail JSON（oracle_type='performance'，零 DDL）。不 raise。
  - build_perf_baseline：当前 run vs 最近 window 个 run 的同 URL 性能对比
    （均值/p50/p95 + 劣化判定）。无历史数据返回 {}。
  - enrich_report_with_perf：并入 Step5 stats（PERF_BASELINE_ENABLED 门控）。

数据源：oracle_results.detail JSON（schema.sql:1799 已存在）。

分位数算法（整改 B2）
--------------------
本模块**不再自带**分位数实现，统一转发到唯一真源
``src.extensions.perf_baseline.schemas.percentile``（线性插值，numpy 兼容）。

.. warning::
   **这是一次有意的数值行为变更**。原实现 ``idx = int(len(s) * pct)`` 是朴素索引取整，
   与真源的线性插值对同一组样本会给出**不同的 P50/P95/P99**。
   例：``[1000, 1100]`` 的 P50 旧算法 = ``1100``，新算法 = ``1050``。
   因此 :func:`build_perf_baseline` 的返回值携带 ``percentile_algo`` 标识，
   **本轮之后的报告不可与历史报告的分位数直接比较**。

.. note::
   本模块与 ``src/extensions/perf_baseline/`` 是**不同数据源的两条链路**，不是重复实现：
   本模块读 MySQL ``oracle_results``（跨 run 对比），extensions 包读
   ``PerfCollectorPlugin -> PerfContext`` 的文件基线（门禁）。真正重复的只有分位数算法，
   已在此收敛；其余 DB 查询与逐 URL 对比逻辑为本模块独有，无处可转发。

[已复核-DeepSeek-V3 2026-08-16]
"""
from __future__ import annotations
from src.core.guarded import guard

import json
import logging
import statistics
from typing import Any

# 整改 B2：分位数唯一真源（线性插值）。本模块只做 0~1 → 0~100 的单位适配。
from src.extensions.perf_baseline.schemas import (
    PERCENTILE_ALGO,
    percentile as _percentile,
)

_log = logging.getLogger(__name__)


def _pct(values: list[float], pct: float) -> float:
    """分位数（**转发到唯一真源**，保留原函数名与 0~1 小数入参约定）。

    参数：
        values: 观测值列表，允许为空。
        pct: 分位点，**0~1 的小数**（如 ``0.95`` 表示 P95）——沿用本模块历史约定；
             真源 :func:`percentile` 用的是 0~100，此处负责单位换算。

    返回：
        分位值；空列表返回 ``0.0``。

    .. warning::
       结果为线性插值，与整改前的朴素索引取整**不等值**。详见模块 docstring。
    """
    if not values:
        return 0.0
    return _percentile(list(values), float(pct) * 100.0)


def collect_perf_metrics(execution_id: int, detail: dict) -> None:
    """把 {page_load_ms, api: {url: elapsed_ms}} 写入 oracle_results.detail JSON。

    oracle_type='performance'；execution_id 无效/写失败 → 仅记 warning（不 raise）。
    """
    if not execution_id or not isinstance(detail, dict):
        return
    with guard("collect_perf_metrics: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection

        conn = None
        try:
            conn = get_connection()
            raw = json.dumps(detail, ensure_ascii=False)
            conn.execute(
                "INSERT INTO oracle_results "
                "(project, version_id, execution_id, oracle_type, detail, passed) "
                "SELECT project, version_id, %s, 'performance', %s, 1 "
                "FROM executions WHERE id=%s LIMIT 1",
                (execution_id, raw, execution_id),
            )
            conn.commit()
        finally:
            if conn:
                try:
                    conn.close()
                except (OSError, ConnectionError) as _e:
                    _log.debug("[perf_baseline] conn.close: %s", _e)


def _fetch_perf_details(project: str, run_id: str, window: int) -> list[dict]:
    """拉取指定 run 的性能 detail（oracle_results.detail JSON 解析）。"""
    if window < 1:
        window = 1
    try:
        from src.core.db_manager import get_connection

        conn = None
        try:
            conn = get_connection()
            rows = (
                conn.execute(
                    "SELECT o.run_id, o.detail FROM oracle_results o "
                    "INNER JOIN executions e ON e.id = o.execution_id "
                    "WHERE o.oracle_type='performance' AND e.project=%s "
                    "ORDER BY o.id DESC LIMIT %s",
                    (project, window * 200),
                ).fetchall()
                or []
            )
            out = []
            for r in rows:
                detail = r.get("detail")
                if not detail:
                    continue
                if isinstance(detail, str):
                    try:
                        detail = json.loads(detail)
                    except (ValueError, TypeError):
                        continue
                if isinstance(detail, dict):
                    out.append({"run_id": r.get("run_id", ""), "detail": detail})
            return out
        finally:
            if conn:
                try:
                    conn.close()
                except (OSError, ConnectionError) as _e:
                    _log.warning("[perf_baseline] conn.close 失败: %s", _e)
    except Exception as e:  # noqa: BLE001
        _log.warning("[perf_baseline] 拉取性能数据失败: %s", e)
        return []


def build_perf_baseline(project: str, run_id: str, window: int = 1) -> dict[str, Any]:
    """对比当前 run 与最近 window 个 run 的同 URL 性能。

    返回 ``{items: [...], window: int, percentile_algo: str}``；
    无当前 run 数据或无历史 → 返回 ``{}``（不阻断报告）。

    ``percentile_algo``（整改 B2 新增）标识 p50/p95 的计算算法版本，
    消费方（报告页 / 门禁页）据此判断能否与历史数据同口径比较。
    """
    project = str(project or "").strip()
    if not project:
        return {}
    current = _fetch_perf_details(project, run_id or "", window)
    if not current:
        return {}
    # 当前 run 的 API 耗时（取最近一条 detail）
    cur_detail = current[0]["detail"]
    cur_api = cur_detail.get("api") or {}
    cur_urls = sorted(cur_api.keys())
    if not cur_urls:
        return {}

    # 历史 run 的同 URL 数据（排除当前 run）
    hist_by_url: dict[str, list[float]] = {}
    for item in current[1:]:
        hist_api = item.get("detail", {}).get("api") or {}
        for url, ms in hist_api.items():
            if isinstance(ms, (int, float)):
                hist_by_url.setdefault(str(url), []).append(float(ms))

    items = []
    for url in cur_urls:
        cur_ms = float(cur_api.get(url, 0) or 0)
        hist = hist_by_url.get(url, [])
        if not hist:
            items.append(
                {
                    "url": url,
                    "current_mean": round(cur_ms, 1),
                    "baseline_mean": "-",
                    "p50": "-",
                    "p95": "-",
                    "verdict": "no_baseline",
                }
            )
            continue
        base_mean = statistics.mean(hist)
        verdict = "degraded" if cur_ms > base_mean * 1.2 else "ok"
        items.append(
            {
                "url": url,
                "current_mean": round(cur_ms, 1),
                "baseline_mean": round(base_mean, 1),
                "p50": _pct(hist, 0.5),
                "p95": _pct(hist, 0.95),
                "verdict": verdict,
            }
        )
    # 整改 B2：随报告下发分位数算法版本，防止与历史（朴素取整）数据被误当同口径比较。
    return {"items": items, "window": window, "percentile_algo": PERCENTILE_ALGO}


def enrich_report_with_perf(stats: dict, project: str, run_id: str) -> dict:
    """并入 Step5 stats（PERF_BASELINE_ENABLED 门控；无 project 跳过）。"""
    if not isinstance(stats, dict):
        return stats
    project = str(project or "").strip()
    if not project:
        return stats
    try:
        from src.core.pipeline_config import _read_config_value as _cfg

        if not bool(_cfg("PERF_BASELINE_ENABLED", False)):
            return stats
        window = int(_cfg("PERF_BASELINE_WINDOW", 1) or 1)
    except Exception:  # noqa: BLE001
        return stats
    with guard("enrich_report_with_perf: baseline = build_perf_baseline(proje", log=_log, level=logging.WARNING):
        baseline = build_perf_baseline(project, run_id or "", window)
        if baseline:
            stats["perf_baseline"] = baseline
    return stats
