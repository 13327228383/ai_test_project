# -*- coding: utf-8 -*-
"""
Pipeline Bus — 流水线事件总线
==============================
跨阶段的事件发布/订阅机制。

设计原则：
- 每个阶段完成时发布事件
- 插件订阅感兴趣的事件
- 事件携带阶段上下文数据
- 插件异常不影响主流程
"""

import logging
from src.core.guarded import guard
from enum import Enum
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)


class PipelineEvent(str, Enum):
    """流水线阶段事件。"""

    STEP1_START = "step1_start"  # Step1 开始
    STEP1_COMPLETE = "step1_complete"  # Step1 完成（数据拉取完毕）
    STEP2_START = "step2_start"  # Step2 开始
    STEP2_COMPLETE = "step2_complete"  # Step2 完成（需求拆解完毕）
    STEP3_START = "step3_start"  # Step3 开始
    STEP3_COMPLETE = "step3_complete"  # Step3 完成（测试用例生成完毕）
    STEP4_START = "step4_start"  # Step4 开始
    STEP4_COMPLETE = "step4_complete"  # Step4 完成（执行完毕）
    STEP5_START = "step5_start"  # Step5 开始
    STEP5_COMPLETE = "step5_complete"  # Step5 完成（报告生成完毕）
    PIPELINE_COMPLETE = "pipeline_complete"  # 整条流水线完成


class PipelineBus:
    """流水线事件总线。

    ★ B-5-3: publish() 时自动将 PipelineEvent 映射到 PluginHook 并 dispatch，
       使全流水线插件可订阅 Step 级事件。
    ★ 方案C 改造：移除 __new__ 强制单例，允许 PerProjectRegistry 创建多实例。
    """

    # ★ B-5-3: PipelineEvent → PluginHook 映射表
    # 注意: 值是 hook_name（不含 on_ 前缀），on_hook 会自动加 on_
    _EVENT_HOOK_MAP = {
        "step4_start": "before_intent",
        "step4_complete": "after_intent",
        "step5_complete": "verify",
    }

    def __init__(self):
        self._subscribers: dict[PipelineEvent, list[Callable]] = {}
        self._initialized = False

    def subscribe(self, event: PipelineEvent, handler: Callable) -> None:
        """订阅事件。"""
        if event not in self._subscribers:
            self._subscribers[event] = []
        self._subscribers[event].append(handler)
        _log.debug(
            "[PipelineBus] %s 订阅者 +1 (共 %d)",
            event.value,
            len(self._subscribers[event]),
        )

    def unsubscribe(self, event: PipelineEvent, handler: Callable) -> None:
        """取消订阅。"""
        if event in self._subscribers:
            self._subscribers[event] = [
                h for h in self._subscribers[event] if h != handler
            ]

    def publish(self, event: PipelineEvent, context: dict[str, Any]) -> list[Any]:
        """
        发布事件。依次调用所有订阅者。

        ★ B-5-3: 同时将事件映射到 PluginHook 并通过 plugin_manager dispatch，
           使 Step4 级插件也能响应流水线级事件。

        返回：
            所有订阅者的返回结果列表
        """
        results = []
        handlers = self._subscribers.get(event, [])
        if not handlers:
            _log.debug("[PipelineBus] %s 无订阅者", event.value)
        else:
            _log.info("[PipelineBus] 📢 %s → %d 个订阅者", event.value, len(handlers))
            for handler in handlers:
                with guard("publish: result = handler(event, context)", log=_log, level=logging.WARNING):
                    result = handler(event, context)
                    if result is not None:
                        results.append(result)

        # ★ B-5-3: 自动 dispatch 对应 PluginHook
        hook_name = self._EVENT_HOOK_MAP.get(event.value)
        if hook_name:
            with guard("publish: from src.pipeline.steps.step4.plugin", log=_log, level=logging.WARNING):
                from src.pipeline.steps.step4.plugins.plugin_manager import (
                    plugin_manager,
                )

                plugin_manager.emit(hook_name, context)

        return results

    def get_subscribers(self, event: PipelineEvent) -> list[Callable]:
        """获取某事件的所有订阅者。"""
        return self._subscribers.get(event, [])

    def reset(self) -> None:
        """清空所有订阅者（主要用于测试）。"""
        self._subscribers.clear()


# ★ 方案C 改造：pipeline_bus 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

pipeline_bus = PerProjectRegistry(PipelineBus)
