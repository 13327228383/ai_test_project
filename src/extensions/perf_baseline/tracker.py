# -*- coding: utf-8 -*-
"""性能追踪器 — 记录每条测试用例的执行时间"""

import time
import json
import os
import logging
from datetime import datetime
from typing import Optional
from collections import defaultdict

_log = logging.getLogger(__name__)


class PerfTracker:
    """性能追踪器：记录测试用例执行耗时，支持嵌套和分组"""

    def __init__(self, storage_dir: Optional[str] = None):
        self.storage_dir = storage_dir or os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "data", "perf_baseline"
        )
        os.makedirs(self.storage_dir, exist_ok=True)
        self._records: list[dict] = []
        self._running: dict[str, float] = {}  # case_id -> start_time

    def start(self, case_id: str):
        """开始计时"""
        self._running[case_id] = time.time()

    def stop(self, case_id: str, metadata: Optional[dict] = None) -> dict:
        """结束计时，返回记录"""
        if case_id not in self._running:
            return None  # 静默跳过，不输出警告{}

        elapsed_ms = (time.time() - self._running[case_id]) * 1000
        del self._running[case_id]

        record = {
            "case_id": case_id,
            "elapsed_ms": round(elapsed_ms, 2),
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        self._records.append(record)
        return record

    def get_records(self) -> list[dict]:
        return self._records

    def get_stats(self) -> dict:
        """计算统计信息"""
        if not self._records:
            return {
                "count": 0,
                "min_ms": 0,
                "max_ms": 0,
                "avg_ms": 0,
                "p50_ms": 0,
                "p95_ms": 0,
                "p99_ms": 0,
            }

        elapsed = sorted([r["elapsed_ms"] for r in self._records])
        n = len(elapsed)
        return {
            "count": n,
            "min_ms": round(elapsed[0], 2),
            "max_ms": round(elapsed[-1], 2),
            "avg_ms": round(sum(elapsed) / n, 2),
            "p50_ms": round(elapsed[int(n * 0.50)], 2),
            "p95_ms": round(elapsed[min(int(n * 0.95), n - 1)], 2),
            "p99_ms": round(elapsed[min(int(n * 0.99), n - 1)], 2),
            "total_ms": round(sum(elapsed), 2),
        }

    def save(self, run_id: Optional[str] = None) -> str:
        """持久化本次运行记录"""
        run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(self.storage_dir, f"perf_{run_id}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "run_id": run_id,
                    "stats": self.get_stats(),
                    "records": self._records,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        _log.info("PerfTracker: saved %d records to %s", len(self._records), filepath)
        return filepath

    def get_module_stats(self) -> dict:
        """按模块分组统计"""
        module_map: dict[str, list[float]] = defaultdict(list)
        for r in self._records:
            mod = r.get("metadata", {}).get("module", "unknown")
            module_map[mod].append(r["elapsed_ms"])

        result = {}
        for mod, values in module_map.items():
            result[mod] = {
                "count": len(values),
                "avg_ms": round(sum(values) / len(values), 2),
                "max_ms": round(max(values), 2),
                "min_ms": round(min(values), 2),
            }
        return result
