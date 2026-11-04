# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
性能监控插件
=============
采集页面性能指标（FCP、LCP、TTFB 等），检测性能回归。

介入点：AFTER_NAVIGATE（页面加载后）、AFTER_STEP（步骤完成后）
存储：MySQL / 内存
"""

import logging
import json
from typing import Any

_log = logging.getLogger(__name__)


class PerformanceMonitorPlugin:
    """性能监控插件。"""

    name = "performance_monitor"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        # 性能指标阈值（毫秒）
        self._thresholds = self.config.get(
            "thresholds",
            {
                "FCP": 1800,  # First Contentful Paint
                "LCP": 2500,  # Largest Contentful Paint
                "TTFB": 600,  # Time to First Byte
                "DOMContentLoaded": 2000,
                "LoadEvent": 3000,
            },
        )
        # 性能数据存储
        self._metrics: dict[str, list] = {}

    def on_navigate(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        页面导航后采集性能指标。

        ctx 包含:
            url: str — 导航目标 URL
            engine: PlaywrightEngine — 浏览器引擎
            page_state: dict — 页面状态
        """
        if not self.enabled:
            return None

        engine = ctx.get("engine")
        if engine is None or not hasattr(engine, "_page"):
            return None

        page = engine._page
        if page is None:
            return None

        try:
            metrics = self._collect_metrics(page)
            if metrics:
                url = ctx.get("url", "unknown")
                _log.info(
                    "[Performance] %s — FCP:%.0fms LCP:%.0fms TTFB:%.0fms DOM:%.0fms Load:%.0fms",
                    url[:60],
                    metrics.get("FCP", 0),
                    metrics.get("LCP", 0),
                    metrics.get("TTFB", 0),
                    metrics.get("DOMContentLoaded", 0),
                    metrics.get("LoadEvent", 0),
                )

                # 记录 FCP 到 ai_health_metrics
                self._record_fcp_metric(metrics)

                # 检查阈值
                violations = self._check_thresholds(metrics)
                if violations:
                    _log.warning(
                        "[Performance] ⚠ 性能指标超出阈值: %s",
                        ", ".join(violations),
                    )
                    return {
                        "performance_metrics": metrics,
                        "performance_violations": violations,
                    }

                return {"performance_metrics": metrics}

        except Exception as e:
            _log.debug("[Performance] 采集异常: %s", str(e)[:80])
            return None

    def on_after_step(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """步骤完成后记录性能数据。"""
        case_id = ctx.get("case_id", "unknown")
        step_idx = ctx.get("step_index", 0)
        metrics = ctx.get("performance_metrics", {})

        if metrics:
            key = f"{case_id}_step{step_idx}"
            self._metrics[key] = metrics

        return None

    def _record_fcp_metric(self, metrics: dict[str, float]):
        """记录 FCP 性能指标到 ai_health_metrics。"""
        with guard("_record_fcp_metric: fcp_ms = metrics.get('FCP', 0)", log=_log):
            fcp_ms = metrics.get("FCP", 0)
            if not fcp_ms:
                return
            from src.backend.state import _thread_run_id
            from src.extensions.metrics_collector import metrics_collector

            run_id = getattr(_thread_run_id, "value", "") or ""
            if run_id:
                exceeds = fcp_ms > self._thresholds.get("FCP", 1800)
                metrics_collector.record_performance_fcp(
                    project=self._get_project(),
                    run_id=run_id,
                    case_id="",
                    fcp_ms=fcp_ms,
                    exceeds_threshold=exceeds,
                )

    def _get_project(self) -> str:
        """获取当前项目名。"""
        try:
            from src.core.config_loader import config

            return config.project_name
        except Exception as e:
            _log.warning("性能监控项目名获取异常: %s", e, exc_info=True)
            return ""

    def _collect_metrics(self, page) -> dict[str, float] | None:
        """通过 Playwright 采集页面性能指标。"""
        with guard("_collect_metrics: metrics_js = ''' () => { const nav =", log=_log):
            # 使用 Performance API 获取指标
            metrics_js = """
            () => {
                const nav = performance.getEntriesByType('navigation')[0];
                const paint = performance.getEntriesByType('paint');
                const lcp = performance.getEntriesByType('largest-contentful-paint');

                const result = {};

                if (nav) {
                    result.TTFB = nav.responseStart - nav.startTime;
                    result.DOMContentLoaded = nav.domContentLoadedEventEnd - nav.startTime;
                    result.LoadEvent = nav.loadEventEnd - nav.startTime;
                    result.DOMInteractive = nav.domInteractive - nav.startTime;
                }

                paint.forEach(p => {
                    if (p.name === 'first-contentful-paint') {
                        result.FCP = p.startTime;
                    }
                });

                if (lcp.length > 0) {
                    const last = lcp[lcp.length - 1];
                    result.LCP = last.startTime;
                }

                return result;
            }
            """
            raw = page.evaluate(metrics_js)
            if raw:
                return {k: float(v) for k, v in raw.items() if v is not None}

        # Fallback: 使用 Playwright 内置 performance metrics
        try:
            perf = page.evaluate("() => JSON.stringify(performance.toJSON())")
            if perf:
                data = json.loads(perf)
                timing = data.get("timing", {})
                return {
                    "TTFB": timing.get("responseStart", 0)
                    - timing.get("navigationStart", 0),
                    "DOMContentLoaded": timing.get("domContentLoadedEventEnd", 0)
                    - timing.get("navigationStart", 0),
                    "LoadEvent": timing.get("loadEventEnd", 0)
                    - timing.get("navigationStart", 0),
                }
        except Exception as _e:
            _log.debug("[performance_monitor] 非关键异常: %s", _e, exc_info=True)

        return None

    def _check_thresholds(self, metrics: dict[str, float]) -> list:
        """检查性能指标是否超出阈值。"""
        violations = []
        for metric, value in metrics.items():
            threshold = self._thresholds.get(metric)
            if threshold and value > threshold:
                violations.append(f"{metric}={value:.0f}ms > {threshold}ms")
        return violations

    def get_summary(self, case_id: str = None) -> dict[str, Any]:
        """获取性能数据摘要（供前端展示）。"""
        if case_id:
            keys = [k for k in self._metrics if k.startswith(case_id)]
        else:
            keys = list(self._metrics.keys())

        if not keys:
            return {"total_records": 0}

        all_metrics = [self._metrics[k] for k in keys]

        summary = {"total_records": len(keys), "metrics": {}}
        for metric_name in ["FCP", "LCP", "TTFB", "DOMContentLoaded", "LoadEvent"]:
            values = [
                m.get(metric_name)
                for m in all_metrics
                if m.get(metric_name) is not None
            ]
            if values:
                summary["metrics"][metric_name] = {
                    "avg": round(sum(values) / len(values), 2),
                    "min": round(min(values), 2),
                    "max": round(max(values), 2),
                    "count": len(values),
                }

        return summary
