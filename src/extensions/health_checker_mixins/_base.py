# -*- coding: utf-8 -*-
"""HealthChecker 基础 mixin（单例、缓存、结果结构、批量检查）。"""

import threading
import time


class HealthCheckerBaseMixin:
    """提供结果封装、缓存与批量并发检查。

    ★ 方案C 改造：移除 __new__ 强制单例，允许 PerProjectRegistry 创建多实例。
    """

    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._cache_lock = threading.Lock()
        self._cache_ts = 0.0
        self._cache_ttl = 30.0  # 缓存 30 秒
        self._initialized = True

    @staticmethod
    def _result(name: str, up: bool, latency_ms: float, error_msg: str = "") -> dict:
        return {
            "name": name,
            "status": "up" if up else "down",
            "latency_ms": round(latency_ms, 2),
            "error_msg": error_msg,
        }

    def _cache_result(self, results: dict[str, dict]) -> None:
        """将结果写入缓存。"""
        with self._cache_lock:
            self._cache = dict(results)
            self._cache_ts = time.time()

    def _get_cached(self) -> list[dict]:
        """若缓存未过期则返回缓存结果，否则返回空列表。"""
        now = time.time()
        with self._cache_lock:
            if (now - self._cache_ts) < self._cache_ttl and self._cache:
                return list(self._cache.values())
        return []

    def _check_all(self, checks: list[tuple]) -> list[dict]:
        """
        并发执行所有检查（I/O 密集型，适合线程）。

        checks: [(name, callable), ...]
        返回: 每个依赖的检查结果列表。
        """
        results: dict[str, dict] = {}
        threads = []

        def _run(name: str, fn):
            try:
                results[name] = fn()
            except Exception as e:
                results[name] = self._result(name, False, 0, str(e)[:200])

        for name, fn in checks:
            t = threading.Thread(target=_run, args=(name, fn), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=self.TIMEOUT + 5)

        # 确保所有项都有结果（防止线程异常遗漏）
        for name, _ in checks:
            if name not in results:
                results[name] = self._result(name, False, 0, "检查线程未返回")

        self._cache_result(results)
        return list(results.values())
