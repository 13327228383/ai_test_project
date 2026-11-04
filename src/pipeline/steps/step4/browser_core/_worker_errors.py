# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard
from src.pipeline.steps.step4.playwright_engine.lifecycle import _STATIC_SUFFIX as _STATIC_SUFFIX  # noqa: F401  词表唯一源
from src.pipeline.steps.step4.playwright_engine.js_load_phase import PAGE_LOAD_KEY

"""并行 worker 级 JS 报错缓冲（task-local 隔离）。

GAP-01 并行路径（browser_core/parallel.py）每个 worker 是独立 asyncio Task。
若所有 worker 的 console/pageerror 都写入共享 engine._console_errors，同批
并发 worker 会互相污染（干净用例可能读到同批脏用例的 JS 报错而误 FAIL）。

本模块提供（[未复核-需 GLM/DeepSeek 把关]）：
- set_worker_errors(errors)      — worker 启动时把「自己的缓冲」绑定到当前 task
- get_worker_errors()            — 用例消费时只读当前 task 的缓冲（无并行上下文
                                   返回 None → 消费方回落 engine._console_errors，
                                   与串行路径语义完全一致）
- make_worker_handlers(errors)   — 构造与 lifecycle.py _on_console/_on_pageerror
                                   同语义的 worker 级处理器（仅缓存归属不同：
                                   worker 缓冲而非 engine._console_errors）

实现要点：
- contextvars 按 asyncio task 隔离：并行 worker 之间互不可见，串行路径
  （无 set_worker_errors 调用）不受影响。
- 条目结构保持与 lifecycle.py 完全一致
  （{"type": "console.error"|"pageerror", "text": ..., ...}），零数据契约变更。

网络日志（C5）沿用同一隔离范式：
- set_worker_network / get_worker_network：worker 级 request/response 缓冲
- make_worker_network_handlers：构造与 lifecycle._on_request/_on_response
  同语义的 worker 级处理器（仅缓存归属为 worker 独立缓冲）
"""

import contextvars
import json
import logging
import time
from datetime import datetime

_log = logging.getLogger(__name__)

_worker_errors_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "parallel_worker_errors", default=None
)

# ★ C5 [未复核-需 GLM/DeepSeek 把关]: 并行 worker 级网络日志缓冲（task 隔离）
_worker_network_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "parallel_worker_network", default=None
)



def set_worker_errors(errors) -> None:
    """绑定当前 asyncio task 的 worker 级 JS 报错缓冲。

    Args:
        errors: list | None。并行 worker 传独立 list；串行回落可传 None。
    """
    _worker_errors_var.set(errors)


def get_worker_errors():
    """读取当前 asyncio task 的 worker 级 JS 报错缓冲。

    Returns:
        list | None：并行路径返回 worker 独立缓冲；串行路径（未绑定）返回 None，
        由消费方回落 engine._console_errors（原语义不变）。
    """
    return _worker_errors_var.get()


def set_worker_network(network) -> None:
    """绑定当前 asyncio task 的 worker 级网络日志缓冲（C5）。"""
    _worker_network_var.set(network)


def get_worker_network():
    """读取当前 asyncio task 的 worker 级网络日志缓冲（C5）。

    Returns:
        list | None：并行路径返回 worker 独立缓冲；串行路径（未绑定）返回 None。
    """
    return _worker_network_var.get()


def make_worker_handlers(errors, load_phase=None):
    """构造 worker 级 console/pageerror 处理器（与 lifecycle.py 同语义）。

    Args:
        errors: 目标缓冲 list（每个 worker 独立实例）。
        load_phase: PageLoadPhaseTracker（判据唯一源 js_load_phase）。用于给每条
            报错打「装载期」归属标记；None 时等价于「始终非装载期」（保持旧行为）。

    Returns:
        (on_console, on_pageerror) 两个同步回调，供 page.on("console") /
        page.on("pageerror") 挂载。缓存条目结构与 lifecycle.py _on_console /
        _on_pageerror 完全一致，仅写入目标改为 worker 自己的缓冲。
    """

    def _is_load_phase() -> bool:
        return bool(load_phase.is_load_phase()) if load_phase is not None else False

    def on_console(msg) -> None:
        """监听浏览器 console 消息，缓存 error 级别日志（同 lifecycle._on_console）。"""
        with guard("on_console: if msg.type == 'error': errors.appen", log=_log):
            if msg.type == "error":
                errors.append(
                    {
                        "type": "console.error",
                        "text": msg.text or "",
                        "url": (
                            msg.location.get("url", "")
                            if hasattr(msg, "location") and msg.location
                            else ""
                        ),
                        PAGE_LOAD_KEY: _is_load_phase(),
                    }
                )

    def on_pageerror(err) -> None:
        """监听未捕获 JS 异常（同 lifecycle._on_pageerror）。"""
        with guard("on_pageerror: errors.append( { 'type': 'pageerror'", log=_log):
            errors.append(
                {
                    "type": "pageerror",
                    "text": (
                        str(err.message or err)
                        if hasattr(err, "message")
                        else str(err)
                    ),
                    "stack": str(err.stack or "") if hasattr(err, "stack") else "",
                    PAGE_LOAD_KEY: _is_load_phase(),
                }
            )

    return on_console, on_pageerror


def make_worker_network_handlers(network):
    """★ C5 [未复核-需 GLM/DeepSeek 把关]: 构造 worker 级 request/response 处理器。

    与 lifecycle.py _on_request / _on_response 同语义、同条目结构，仅写入目标
    改为 worker 自己的网络缓冲（经 set_worker_network 绑定，task-local 隔离）。

    Args:
        network: 目标缓冲 list（每个 worker 独立实例）。

    Returns:
        (on_request, on_response) 两个回调（request 同步、response 异步），
        供 page.on("request") / page.on("response") 挂载。
    """

    def on_request(request) -> None:
        """监听 HTTP 请求，留存（含 post_data，跳过静态资源）。"""
        with guard("on_request: url = request.url or ''", log=_log):
            url = request.url or ""
            path = (url.split("?", 1)[0].split("#", 1)[0]).lower()
            if any(path.endswith(s) for s in _STATIC_SUFFIX) or "favicon" in path:
                return
            post_data = None
            with guard("on_request: pd = request.post_data", log=_log):
                pd = request.post_data
                if pd:
                    try:
                        post_data = json.loads(pd)
                    except (ValueError, TypeError):
                        post_data = pd[:500]
            network.append(
                {
                    "event": "request",
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "method": (request.method or "").upper(),
                    "url": url,
                    "post_data": post_data,
                }
            )

    async def on_response(response) -> None:
        """监听 HTTP 响应，与最近同 URL 请求配对，留存状态/耗时/响应体。"""
        with guard("on_response: url = response.url or ''", log=_log):
            url = response.url or ""
            path = (url.split("?", 1)[0].split("#", 1)[0]).lower()
            if any(path.endswith(s) for s in _STATIC_SUFFIX) or "favicon" in path:
                return
            now = time.time()
            paired_ts = None
            elapsed_ms = None
            for rec in reversed(network):
                if (
                    rec.get("event") == "request"
                    and rec.get("url") == url
                    and "elapsed_ms" not in rec
                ):
                    paired_ts = rec.get("ts")
                    try:
                        _pt = datetime.fromisoformat(paired_ts) if paired_ts else None
                        if _pt is not None:
                            elapsed_ms = max(int((now - _pt.timestamp()) * 1000), 0)
                    except Exception:
                        elapsed_ms = None
                    break
            resp_headers = {}
            resp_body = None
            with guard("on_response: resp_headers = dict(response.headers", log=_log):
                resp_headers = dict(response.headers or {})
            with guard("on_response: body = await response.text()", log=_log):
                body = await response.text()
                if body and len(body) < 10000:
                    try:
                        resp_body = json.loads(body)
                    except (ValueError, TypeError):
                        resp_body = body[:500]
            network.append(
                {
                    "event": "response",
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "method": (
                        response.request.method
                        if hasattr(response, "request")
                        else ""
                    ).upper(),
                    "url": url,
                    "status": int(getattr(response, "status", 0) or 0),
                    "elapsed_ms": elapsed_ms,
                    "resp_headers": resp_headers,
                    "resp_body": resp_body,
                }
            )

    return on_request, on_response
