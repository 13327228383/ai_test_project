# -*- coding: utf-8 -*-
"""性能回归检测器 — 实时检测性能退化"""

import logging
from typing import Optional

_log = logging.getLogger(__name__)


class RegressionDetector:
    """实时检测性能回归，支持多种检测策略"""

    def __init__(self, baseline_manager=None):
        self._baseline = baseline_manager
        self._alerts: list[dict] = []

    def check(
        self, case_id: str, elapsed_ms: float, module: str = ""
    ) -> Optional[dict]:
        """检测单条用例是否性能回归"""
        if not self._baseline:
            return None

        baseline = self._baseline.get_case_baseline(case_id)
        if not baseline:
            return None

        avg = baseline["avg_ms"]
        if avg == 0:
            return None

        ratio = elapsed_ms / avg

        # 阈值策略: >1.5x = 轻微, >2.0x = 警告, >3.0x = 严重
        if ratio < 1.5:
            return None

        severity = (
            "critical" if ratio > 3.0 else ("warning" if ratio > 2.0 else "minor")
        )
        alert = {
            "case_id": case_id,
            "module": module,
            "elapsed_ms": elapsed_ms,
            "baseline_avg_ms": avg,
            "ratio": round(ratio, 2),
            "severity": severity,
            "message": f"[{severity.upper()}] {case_id}: {elapsed_ms}ms vs baseline {avg}ms ({ratio:.1f}x)",
        }
        self._alerts.append(alert)
        _log.warning(alert["message"])
        return alert

    def check_all(self, records: list[dict]) -> list[dict]:
        """批量检测"""
        self._alerts = []
        for r in records:
            self.check(
                r["case_id"],
                r["elapsed_ms"],
                r.get("metadata", {}).get("module", ""),
            )
        return self._alerts

    def get_alerts(self) -> list[dict]:
        return self._alerts

    def get_summary(self) -> dict:
        """获取回归检测摘要"""
        critical = sum(1 for a in self._alerts if a["severity"] == "critical")
        warning = sum(1 for a in self._alerts if a["severity"] == "warning")
        minor = sum(1 for a in self._alerts if a["severity"] == "minor")
        return {
            "total_alerts": len(self._alerts),
            "critical": critical,
            "warning": warning,
            "minor": minor,
            "alerts": self._alerts,
        }
