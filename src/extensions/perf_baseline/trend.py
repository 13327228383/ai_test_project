# -*- coding: utf-8 -*-
"""趋势分析器 — 分析执行时间变化趋势"""

import json
import os
import logging
from datetime import datetime
from typing import Optional
from collections import defaultdict

_log = logging.getLogger(__name__)


class TrendAnalyzer:
    """分析性能趋势：识别持续变慢的用例、预测未来性能"""

    def __init__(self, storage_dir: Optional[str] = None):
        self.storage_dir = storage_dir or os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "data", "perf_baseline"
        )
        os.makedirs(self.storage_dir, exist_ok=True)

    def load_history(self, limit: int = 20) -> list[dict]:
        """加载历史运行记录"""
        files = sorted(
            [
                f
                for f in os.listdir(self.storage_dir)
                if f.startswith("perf_") and f.endswith(".json")
            ],
            reverse=True,
        )[:limit]

        history = []
        for f in files:
            with open(os.path.join(self.storage_dir, f), "r", encoding="utf-8") as fp:
                history.append(json.load(fp))
        return history

    def analyze_trend(self, case_id: str, runs: list[dict]) -> dict:
        """分析单个用例的趋势"""
        data_points = []
        for run in runs:
            for r in run.get("records", []):
                if r["case_id"] == case_id:
                    data_points.append(
                        {
                            "run_id": run["run_id"],
                            "elapsed_ms": r["elapsed_ms"],
                            "timestamp": r.get("timestamp", run["run_id"]),
                        }
                    )

        if len(data_points) < 2:
            return {
                "case_id": case_id,
                "trend": "insufficient_data",
                "data_points": len(data_points),
            }

        # 简单线性趋势: 比较最近3次和最早3次的平均值
        recent = data_points[:3]
        earliest = data_points[-3:]

        recent_avg = sum(p["elapsed_ms"] for p in recent) / len(recent)
        earliest_avg = sum(p["elapsed_ms"] for p in earliest) / len(earliest)

        if earliest_avg == 0:
            return {
                "case_id": case_id,
                "trend": "stable",
                "data_points": len(data_points),
            }

        ratio = recent_avg / earliest_avg

        if ratio > 1.3:
            trend = "degrading"
        elif ratio < 0.7:
            trend = "improving"
        else:
            trend = "stable"

        return {
            "case_id": case_id,
            "trend": trend,
            "data_points": len(data_points),
            "recent_avg_ms": round(recent_avg, 2),
            "earliest_avg_ms": round(earliest_avg, 2),
            "ratio": round(ratio, 2),
        }

    def analyze_all(self) -> dict:
        """分析所有用例的趋势"""
        history = self.load_history()
        if not history:
            return {"error": "no_history", "message": "无历史运行记录"}

        # 收集所有 case_id
        all_cases: set[str] = set()
        for run in history:
            for r in run.get("records", []):
                all_cases.add(r["case_id"])

        results = {}
        for case_id in sorted(all_cases):
            results[case_id] = self.analyze_trend(case_id, history)

        # 汇总
        summary = defaultdict(list)
        for case_id, result in results.items():
            summary[result.get("trend", "unknown")].append(case_id)

        # 时间序列（正序：最早一次 → 最近一次），供可视化（SVG 折线图）
        series: dict[str, list[dict]] = {}
        for case_id in sorted(all_cases):
            pts: list[dict] = []
            # history 按文件名 reverse=True → 最新在前；此处反转回正序
            for run in reversed(history):
                for r in run.get("records", []):
                    if r["case_id"] == case_id:
                        pts.append(
                            {
                                "elapsed_ms": round(float(r["elapsed_ms"]), 2),
                                "run_id": r.get("run_id"),
                                "timestamp": r.get(
                                    "timestamp", r.get("run_id")
                                ),
                            }
                        )
            series[case_id] = pts

        return {
            "analyzed_at": datetime.now().isoformat(),
            "total_runs": len(history),
            "total_cases": len(all_cases),
            "trends": {
                "degrading": summary.get("degrading", []),
                "improving": summary.get("improving", []),
                "stable": summary.get("stable", []),
                "insufficient_data": summary.get("insufficient_data", []),
            },
            "details": results,
            "series": series,
        }
