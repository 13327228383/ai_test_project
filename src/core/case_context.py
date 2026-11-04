# -*- coding: utf-8 -*-
"""用例执行上下文（ContextVar 单源传递）。

为什么存在
----------
「本用例是否期望被拒绝/不可达」这一条语义，曾在 ≥3 处被各自重新推导
（错误页判定、404 语义判定、访问控制断言极性），导致同一用例在不同环节得到
相反结论（期望 403 的用例被判成"页面错误"→ 重登+菜单回退 → 假 FAIL）。

正解：语义在上游**一次性**判定，落进本上下文；下游**只读不推**。

契约
----
- 唯一写入点：Step4 意图入口 ``single_mixins/_base.py::_execute_one_intent``。
- 唯一生产函数：``step3._identity_assign.case_expects_denial``（勿在本模块复述判据）。
- 读取点：``browser_tools/execution/_single_nav_state.py::_check_error_page``、
  ``agent/explorer_mixins/_action.py`` 的 404 语义分支。
- 用 ``ContextVar`` 而非 engine 属性：并行执行下每个 asyncio Task 各自持有上下文副本，
  用例之间互不串扰（engine 属性在并行路径会互相覆盖）。
"""

from __future__ import annotations

import contextvars

_case_expects_denial: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "e2e_case_expects_denial", default=False
)


def set_case_expects_denial(flag: object) -> None:
    """写入当前用例的「期望被拒绝」语义（唯一写入点调用）。"""
    _case_expects_denial.set(bool(flag))


def current_case_expects_denial() -> bool:
    """读取当前用例的「期望被拒绝」语义（下游唯一读取口）。"""
    return _case_expects_denial.get()
