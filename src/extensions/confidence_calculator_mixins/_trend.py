# -*- coding: utf-8 -*-
"""
置信度计算器 —— 趋势告警 mixin
=====================================
跨版本置信度趋势检测（下降 >15% 自动告警）。从 ``confidence_calculator.py`` 拆分而来，逻辑保持不变。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class ConfidenceCalculatorTrendMixin:
    """趋势告警 mixin：跨版本置信度趋势检测。"""

    # ═══════════════════════════════════════════════════════════════
    # ★ P2-3: 历史趋势告警（下降 >15% 自动标记）
    # ═══════════════════════════════════════════════════════════════

    def check_confidence_trend(
        self, current_avg: float, project_name: str = None
    ) -> dict[str, Any]:
        return self._check_confidence_trend(current_avg, project_name)

    def _check_confidence_trend(
        self, current_avg: float, project_name: str = None
    ) -> dict[str, Any]:
        """
        检查置信度跨版本趋势。
        与上一次执行对比，下降 >15% → 告警。
        返回: {"alert": bool, "trend": str, "prev_avg": float, "delta": float, "delta_pct": float}
        """
        try:
            from src.extensions.metrics_collector import metrics_collector

            if not hasattr(metrics_collector, "get_confidence_trend"):
                return {
                    "alert": False,
                    "trend": "stable",
                    "prev_avg": 0.0,
                    "delta": 0.0,
                    "delta_pct": 0.0,
                }

            trend_data = metrics_collector.get_confidence_trend(project_name)
            if not trend_data or len(trend_data) < 2:
                return {
                    "alert": False,
                    "trend": "insufficient_data",
                    "prev_avg": 0.0,
                    "delta": 0.0,
                    "delta_pct": 0.0,
                }

            # 取最近两次的avg_confidence
            sorted_data = sorted(trend_data, key=lambda d: d.get("ts") or "")
            prev = sorted_data[-2].get("avg_confidence", 0)
            if not prev or prev <= 0:
                return {
                    "alert": False,
                    "trend": "stable",
                    "prev_avg": 0.0,
                    "delta": 0.0,
                    "delta_pct": 0.0,
                }

            delta = current_avg - prev
            delta_pct = round(abs(delta) / prev * 100, 1)

            if delta < -0.15:
                return {
                    "alert": True,
                    "trend": "declining",
                    "prev_avg": round(prev, 3),
                    "curr_avg": round(current_avg, 3),
                    "delta": round(delta, 3),
                    "delta_pct": delta_pct,
                    "message": (
                        "⚠️ 置信度大幅下降：当前 %.1f%% → 上期 %.1f%%（下降 %.1f%%）。"
                        "建议排查：需求文档变更 / 模型切换 / 数据Schema变化。"
                    )
                    % (current_avg * 100, prev * 100, delta_pct),
                }
            return {
                "alert": False,
                "trend": (
                    "stable"
                    if abs(delta) < 0.05
                    else ("improving" if delta > 0 else "slightly_declining")
                ),
                "prev_avg": round(prev, 3),
                "curr_avg": round(current_avg, 3),
                "delta": round(delta, 3),
                "delta_pct": delta_pct,
                "message": "",
            }
        except Exception as e:
            _log.warning("趋势分析异常: %s", e, exc_info=True)
            return {
                "alert": False,
                "trend": "error",
                "prev_avg": 0.0,
                "delta": 0.0,
                "delta_pct": 0.0,
            }
