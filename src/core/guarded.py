# -*- coding: utf-8 -*-
"""防御原语 —— best-effort 段落的统一收口。

背景（audits/规模根因诊断_2026-10-04.md）：全仓 ~10.9 万行困在 try/except
防御机器里，同一套「吞异常 + 定级日志 + 继续执行」样板在数百个位点手抄。
本原语把它收敛为一个实现：

    x = None
    with guard("加载导航图", log=_log, level=logging.WARNING):
        x = load_nav_graph(p)

语义与原内联 try/except 完全一致：异常被吞、按级落日志、控制流继续到
with 块之后；body 内的 return/continue/break/raise 语义不变。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

_log = logging.getLogger(__name__)


@contextmanager
def guard(step: str, *, log: logging.Logger | None = None,
          level: int = logging.DEBUG, limit: int = 200):
    """吞掉 body 抛出的 Exception 并按 level 落一行日志；不改变控制流。

    Args:
        step: 步骤名（进日志，定位用）。
        log: 目标 logger；缺省用本模块 _log。
        level: 日志级别（兜底路径建议 DEBUG，关键路径 WARNING）。
        limit: 异常消息截断长度。
    """
    try:
        yield
    except Exception as e:  # noqa: BLE001 —— 本原语的唯一职责就是吞
        (log or _log).log(level, "%s 失败(可容忍): %s", step, str(e)[:limit])
