# -*- coding: utf-8 -*-
"""LLM 不可用性熔断（进程级单例，供语义辅助层快速失败）。

why: 语义辅助调用（菜单/元素匹配/断言）有"降级关键词兜底"，LLM 不可用时
本应快速失败；但每次调用都重新付满 cloud_timeout(20s) 超时才降级，
多步用例（14 步 × 20s）在 LLM 黑洞期间必爆单用例 300s 墙钟预算（实测
TC-REQ-002-UX02）。LLM 是否可用属于一次性事实，应由本模块单一判定，
下游只读不各自重推。
"""
from __future__ import annotations

import threading
import time

_TRIP_AFTER_FAILS: int = 2      # 连续失败 N 次 → 熔断
_COOLDOWN_SECONDS: float = 240.0  # 熔断冷却期，冷却后可重新探测

_lock = threading.Lock()
_consecutive_fails: int = 0
_trip_at: float = 0.0


def note_llm_result(ok: bool) -> None:
    """上报一次 LLM 调用结果，维护连续失败计数与熔断状态。"""
    global _consecutive_fails, _trip_at
    with _lock:
        if ok:
            if _consecutive_fails:
                _consecutive_fails = 0
                _trip_at = 0.0
            return
        _consecutive_fails += 1
        if _consecutive_fails >= _TRIP_AFTER_FAILS:
            _trip_at = time.monotonic()


def should_fast_path() -> bool:
    """熔断中（且冷却未过）→ 语义层跳过 LLM，直接走降级兜底。"""
    with _lock:
        if _trip_at <= 0.0:
            return False
        return (time.monotonic() - _trip_at) < _COOLDOWN_SECONDS


def fast_path_remaining() -> float:
    """熔断剩余冷却秒数（日志/可观测用）。"""
    with _lock:
        if _trip_at <= 0.0:
            return 0.0
        _left = _COOLDOWN_SECONDS - (time.monotonic() - _trip_at)
        return _left if _left > 0.0 else 0.0