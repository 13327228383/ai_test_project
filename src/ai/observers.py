# -*- coding: utf-8 -*-
"""
观察者通知模块
==============
封装 AiBrain 的观察者回调通知逻辑，供 GUI 注册以实现 thinking... 实时反馈。

设计原则：无外部依赖，纯通知逻辑。
"""

from collections.abc import Callable
from src.core.guarded import guard


import logging

_log = logging.getLogger(__name__)


class ObserverNotifier:
    """管理观察者回调的注册与通知。"""

    def __init__(self) -> None:
        self._observers: list[tuple[Callable, Callable]] = []

    def register(self, on_start: Callable = None, on_finish: Callable = None) -> None:
        """
        注册思考状态观察者回调。

        参数：
            on_start:  callable(name: str)          — 思考开始时调用
            on_finish: callable(name, elapsed, usage, cost) — 思考结束时调用
        """
        self._observers.append((on_start, on_finish))

    def notify_start(self, name: str) -> None:
        """通知所有观察者思考开始。"""
        for on_start, _ in self._observers:
            if on_start:
                with guard("notify_start: on_start(name)", log=_log, level=logging.WARNING):
                    on_start(name)

    def notify_finish(
        self, name: str, elapsed: float, usage: dict, cost: float, model: str
    ) -> None:
        """通知所有观察者思考结束。"""
        for _, on_finish in self._observers:
            if on_finish:
                with guard("notify_finish: on_finish(name, elapsed, usage, cost", log=_log, level=logging.WARNING):
                    on_finish(name, elapsed, usage, cost, model)
