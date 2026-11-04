# -*- coding: utf-8 -*-
"""系统资源健康检查 mixin。"""

import time


class HealthCheckerSystemMixin:
    """提供磁盘空间与内存使用率检查。"""

    def check_disk(self, threshold_percent: float = 80.0) -> dict:
        """检查磁盘空间使用率。"""
        t0 = time.time()
        try:
            import psutil

            usage = psutil.disk_usage("/")
            latency = (time.time() - t0) * 1000
            percent = usage.percent
            if percent > threshold_percent:
                return self._result(
                    "disk",
                    False,
                    latency,
                    f"磁盘使用率 {percent:.1f}% > {threshold_percent}%",
                )
            return self._result("disk", True, latency)
        except Exception as e:
            return self._result("disk", False, (time.time() - t0) * 1000, str(e)[:200])

    def check_memory(self, threshold_percent: float = 90.0) -> dict:
        """检查内存使用率。"""
        t0 = time.time()
        try:
            import psutil

            mem = psutil.virtual_memory()
            latency = (time.time() - t0) * 1000
            percent = mem.percent
            if percent > threshold_percent:
                return self._result(
                    "memory",
                    False,
                    latency,
                    f"内存使用率 {percent:.1f}% > {threshold_percent}%",
                )
            return self._result("memory", True, latency)
        except Exception as e:
            return self._result(
                "memory", False, (time.time() - t0) * 1000, str(e)[:200]
            )
