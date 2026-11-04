# -*- coding: utf-8 -*-
"""通用熔断原语（外部依赖不可达 → 快速失败，不重复付超时）。

why: 引擎的不少依赖是**旁路**的（审计写库、指标上报、可选的靶子 DB 探测）：
它们不在用例判定的必经路径上，但一旦不可达，原实现会**每条用例重新付满连接超时**
—— 实测 plugin_interventions 写入失败 113 次累计白烧 202s，占前 4 分钟墙钟的 84%，
单例耗时被它从 ~3s 级抬到 12~16s。

「某依赖是否可用」是一条**一次性事实**，应由本模块单一判定，下游只读不推。

用法：
    _breaker = CircuitBreaker("plugin_interventions")
    if _breaker.allow():
        try: ...
        except Exception: _breaker.note_failure()
        else: _breaker.note_success()
"""

from __future__ import annotations

import logging
import threading
import time

_log = logging.getLogger(__name__)


class CircuitBreaker:
    """进程内可共享的熔断器（线程安全）。"""

    def __init__(
        self,
        name: str,
        trip_after_fails: int = 2,
        cooldown_seconds: float = 240.0,
    ) -> None:
        self._name = name
        self._trip_after = max(1, int(trip_after_fails))
        self._cooldown = float(cooldown_seconds)
        self._lock = threading.Lock()
        self._consecutive_fails = 0
        self._trip_at = 0.0
        self._last_error = ""

    def note_success(self) -> None:
        with self._lock:
            if self._consecutive_fails or self._trip_at:
                _log.info("[熔断] %s 恢复正常，计数清零", self._name)
            self._consecutive_fails = 0
            self._trip_at = 0.0
            self._last_error = ""

    def note_failure(self, error: str = "") -> None:
        with self._lock:
            self._consecutive_fails += 1
            self._last_error = str(error or "")[:200]
            if self._consecutive_fails >= self._trip_after:
                if self._trip_at <= 0.0:
                    _log.warning(
                        "[熔断] %s 连续失败 %d 次 → 熔断 %.0fs（旁路不阻塞主链路）",
                        self._name,
                        self._consecutive_fails,
                        self._cooldown,
                    )
                self._trip_at = time.monotonic()

    def allow(self) -> bool:
        """是否允许发起调用（熔断中且未过冷却 → False）。"""
        with self._lock:
            if self._trip_at <= 0.0:
                return True
            if (time.monotonic() - self._trip_at) < self._cooldown:
                return False
            # 冷却结束 → 放行一次探测，成功即清零（见 note_success）
            return True

    def remaining(self) -> float:
        with self._lock:
            if self._trip_at <= 0.0:
                return 0.0
            _left = self._cooldown - (time.monotonic() - self._trip_at)
            return _left if _left > 0.0 else 0.0

    @property
    def last_error(self) -> str:
        return self._last_error
