# -*- coding: utf-8 -*-
"""
BrowserCore — Step4 浏览器自动化执行核心
=======================================

本模块是系统的"浏览器自动化执行引擎"，负责：
- 测试用例的串行/并行执行
- AI 多轮决策循环
- 插件系统（GroundTruth/VisualBaseline/SelfHealing/LongTermMemory/PerformanceMonitor）
- 结构化动作执行（click/fill/select_option/navigate/upload/assert/verify）
- 证据收集与支配度指标记录

【2026-06-18 重构】
原 ~1257 行的单体类已按执行模式拆分为 core/ 下的独立 mixin：
- _base.py    — 基础：__init__ / 插件初始化 / run_test_cases / 门面委托
- _serial.py  — 串行执行：_execute_all（主循环）
- _parallel.py — 并行执行：_execute_intents_parallel
- _single.py  — 单意图执行：_execute_one_intent（最复杂的 AI 多轮循环）

BrowserCore 继承全部 mixin，对外接口 100% 保持兼容。

【方案B 改造】
原模块级单例 browser_core / browser_executor 改为 BrowserCoreRegistry，
按 (project) 隔离多实例，支持多项目并发执行 Step4。
"""

import threading as _threading
import logging

_log = logging.getLogger(__name__)

from .base import _BrowserCoreBaseMixin
from .serial import _BrowserCoreSerialMixin
from .parallel import _BrowserCoreParallelMixin
from .single import _BrowserCoreSingleMixin


class BrowserCore(
    _BrowserCoreBaseMixin,
    _BrowserCoreSerialMixin,
    _BrowserCoreParallelMixin,
    _BrowserCoreSingleMixin,
):
    """
    浏览器自动化执行核心（门面类）。

    使用方式：
        from src.pipeline.steps.step4.browser_core import BrowserCore
        core = BrowserCore()
        result = core.run_test_cases(candidates)
    """

    pass


class BrowserCoreRegistry:
    """★ 方案B 改造：按 project 隔离的 BrowserCore 注册表。

    原实现：browser_executor 是全局单例，_engine / _force_stop_check /
            _plugin_manager / _allocated_test_accounts 等可变状态在并发时互相覆盖。
    新实现：dict[project] → BrowserCore，每个项目独立执行状态。
    兼容策略：保留所有方法签名，自动从 config.project_name 获取当前 project，
    委托给对应的 BrowserCore 实例。_force_stop_check 赋值也路由到正确实例。
    """

    def __init__(self):
        self._cores: dict[str, BrowserCore] = {}
        self._lock = _threading.Lock()
        # 保留一个默认实例，用于无 project 上下文的兼容调用
        self._default = BrowserCore()

    def for_project(self, project: str) -> BrowserCore:
        """获取指定项目的 BrowserCore（不存在则创建）。"""
        if not project:
            return self._default
        with self._lock:
            if project not in self._cores:
                self._cores[project] = BrowserCore()
            return self._cores[project]

    def remove_project(self, project: str):
        """移除指定项目的 BrowserCore（任务结束后清理）。"""
        with self._lock:
            core = self._cores.pop(project, None)
        # 清理浏览器引擎等资源
        if core and getattr(core, "_engine", None):
            try:
                core._release_test_accounts()
            except Exception as _e:
                _log.debug(
                    "[browser_core] _release_test_accounts 异常: %s", _e, exc_info=True
                )

    def for_current(self) -> BrowserCore:
        """获取当前项目的 BrowserCore（从 config.project_name 推断）。"""
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        except Exception:
            project = ""
        return self.for_project(project)

    def __getattr__(self, name):
        """★ 对所有属性访问和方法调用，委托给当前项目的 BrowserCore。
        这确保 step4_executor.py 等调用方的 browser_executor.xxx 都能正确路由。"""
        return getattr(self.for_current(), name)


# ★ 方案B 改造：browser_core / browser_executor 现在是 BrowserCoreRegistry 实例，
#   保持模块级入口不变，但内部按 project 隔离。
browser_core = BrowserCoreRegistry()

# 历史别名：保持向后兼容（重构前单例名为 browser_executor）
# 调用方：src/backend/executor.py、src/pipeline/step4_executor.py
browser_executor = browser_core
