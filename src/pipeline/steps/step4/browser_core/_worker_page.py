# -*- coding: utf-8 -*-
"""并行 worker 级 page 隔离（task-local，无外部依赖）。

why: 并行 worker 曾经通过 `engine._page = worker_page` 把页面挂到**单例 engine** 上，
并发时互相覆盖 —— worker A 执行到一半读到的可能是 worker B 的 page，
于是操作落在错误页面上，表现为间歇性 ERROR/HARNESS_ERROR（**引擎自造假 FAIL**）。
这条竞态很可能就是并行路径历史上从未真正接线（零调用点）的原因。

本模块只提供 per-task 的「当前 page」视图，与 `_worker_errors` / `_worker_network`
同一 contextvar 隔离范式：**故意放独立文件且零 import**，以免
`playwright_engine.lifecycle` ↔ `browser_core.*` 循环导入
（`_worker_errors` 顶层 import 了 lifecycle）。

下游 191 处 `engine._page` 读取**零改动**：读到的自动是当前 worker 的 page；
无并行上下文（串行路径）时回落到引擎主 page，语义完全不变。
"""

from __future__ import annotations

import contextvars

_worker_page_var: "contextvars.ContextVar" = contextvars.ContextVar(
    "parallel_worker_page", default=None
)


def set_worker_page(page) -> None:
    """把「本 worker 的 page」绑定到当前 asyncio task。"""
    _worker_page_var.set(page)


def get_worker_page():
    """读取当前 asyncio task 的 page；无并行上下文返回 None（由调用方回落主 page）。"""
    return _worker_page_var.get()
