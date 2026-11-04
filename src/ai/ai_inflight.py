"""有界长等待「在飞行」登记：让执行槽看门狗能区分「慢」与「挂死」。

2026-10-27 扩展：除 AI 请求外，**任何有硬上界的长等待**（单用例超时预算等）也在此登记，
语义完全一致——「截止时刻前算进展，到期后看门狗照常生效」。这样看门狗只需读一个
统一判据，不必为每类长等待各加一条豁免分支（那正是补丁的来源）。

why: 看门狗以「N 秒无新日志/执行记录」判停滞。而一次大模型调用期间既不写
     run_logs 也不写 executions —— 云端网关实测单轮 92s、本地模型可达数分钟，
     于是「正常等待模型返回」被误判为「线程挂起」并被强制释放执行槽，
     后续步骤得以与仍在运行的步骤并发，造成同一 project/version 的状态竞争。

     故在 AI 调用的两条出网路径上登记「在飞」：
       · src/ai/router_module/_router.py::Router.route  —— 走 Router 的路径（全部 6 处调用点）
       · src/ai/ai_client_mixins/_base.py::_call_api     —— use_router=False / 无 task_type 的直连 Provider 路径
     看门狗据此把「在飞」直接判为进展，既不再误杀，又仍能兜住真正的挂死。

why: 登记用**心跳**而非「进入时算一个静态截止时刻」。一次 route() 内含云端重试，
     总耗时 = (重试次数+1) × 超时 + 退避；静态上界要么算不准，要么为了保险而放宽到
     形同虚设。心跳每半个周期续期，语义严格等于「此刻确实有请求在网上」。
     为防 Provider 超时被误配成无穷大而获得永久豁免，另设硬上限兜底。
"""
from __future__ import annotations

import itertools
import threading
import time
from contextlib import contextmanager
from typing import Iterator

_LOCK = threading.Lock()
# key → 本 key 的有效截止时刻（epoch 秒）。用 dict 支持并发多请求。
_INFLIGHT: dict[int, float] = {}
# 通用有界等待的截止时刻登记（与 _INFLIGHT 同语义，来源不同）
_BOUNDED: dict[int, float] = {}
# why: 键必须单调递增且唯一。早先写 `id(object())` —— 临时对象随即被 GC，
#      id 会被后续对象复用 → 并发请求撞键 → 外层登记被内层覆盖（实测 150s 被写成 90s，
#      反而更容易误杀）。故改用计数器，永不复用。
_KEY_SEQ = itertools.count(1)

# 心跳周期（秒）：续期间隔 = 周期/2，看门狗只需判断截止时刻是否已过。
_HEARTBEAT_INTERVAL = 20.0
# 硬超时到期后的宽限，覆盖 provider 收尾/异常传播的时间。
_GRACE_SECONDS = 30.0
# 调用方未给出超时时的兜底假设（秒），与 Router._build_provider 的口径一致。
_FALLBACK_TIMEOUT = 180.0


def _refresh(key: int, cap: float) -> None:
    # why: 截止时刻必须被豁免上限钳住。否则最后一次心跳可能把 deadline 推到
    #      cap + 心跳周期，硬上限形同虚设。
    _d = min(time.time() + _HEARTBEAT_INTERVAL, cap)
    with _LOCK:
        _INFLIGHT[key] = _d


def _heartbeat(key: int, stop: threading.Event, cap: float) -> None:
    # why: 超过豁免上限即停止续期，让看门狗重新生效 —— 防止 Provider 超时被配成
    #      无穷大时，一次调用获得永久豁免。
    while time.time() < cap and not stop.wait(_HEARTBEAT_INTERVAL / 2.0):
        _refresh(key, cap)


@contextmanager
def ai_request_inflight(timeout: float | None = None) -> Iterator[None]:
    """登记一次 AI 请求的在飞状态（带心跳续期），退出时清除。

    timeout 为该请求的硬超时秒数，仅用作豁免上限；不填按 _FALLBACK_TIMEOUT 计。
    """
    with _LOCK:
        _key = next(_KEY_SEQ)
    _t0 = time.time()
    _cap = _t0 + max(float(timeout or 0.0) or _FALLBACK_TIMEOUT, 0.0) + _GRACE_SECONDS
    _stop = threading.Event()
    _refresh(_key, _cap)
    _hb = threading.Thread(
        target=_heartbeat, args=(_key, _stop, _cap), daemon=True, name="ai-inflight-hb"
    )
    _hb.start()
    try:
        yield
    finally:
        _stop.set()
        with _LOCK:
            _INFLIGHT.pop(_key, None)
        # why: 心跳线程只被 stop 唤醒，若不 join 会在解释器退出时留下悬挂线程；
        #      daemon=True 已保证不阻塞退出，这里 join 只为确定性清理。
        #      豁免上限已过 → 心跳早已停摆，不必等。
        if time.time() < _cap:
            _hb.join(timeout=_HEARTBEAT_INTERVAL)


def inflight_deadline() -> float:
    """当前仍在飞行中的**有界等待**（AI 请求 / 用例超时预算等）的最晚有效截止时刻；无则 0。"""
    with _LOCK:
        _vals = list(_INFLIGHT.values()) + list(_BOUNDED.values())
        return max(_vals) if _vals else 0.0


@contextmanager
def bounded_wait_inflight(timeout: float) -> Iterator[None]:
    """登记一次**已知硬上界**的长等待（调用方保证 ``timeout`` 秒后必然收敛）。

    why: 单用例在浏览器内长时间操作（导航/页面查询/等待）期间同样不落 run_logs，
        看门狗会把「在等一个终将到期的操作」误判为线程挂死，并**杀掉整轮 run**——
        终止粒度错了（该终止的是该用例，不是整个 run）。
     语义与 AI 在飞一致：截止时刻前计为进展；到期后调用方仍无进展（如被不可取消的
     同步调用卡住），看门狗照常生效。故本函数只登记上界，不做任何豁免/放宽。
    """
    with _LOCK:
        _key = next(_KEY_SEQ)
        _BOUNDED[_key] = time.time() + max(float(timeout or 0.0), 0.0)
    try:
        yield
    finally:
        with _LOCK:
            _BOUNDED.pop(_key, None)


def ai_inflight_active() -> bool:
    """此刻是否有 AI 请求在飞行中。"""
    return inflight_deadline() > time.time()
