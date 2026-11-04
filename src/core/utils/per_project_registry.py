# -*- coding: utf-8 -*-
"""
PerProjectRegistry — 通用按 project 隔离的单例注册表
=====================================================

★ 方案C 改造：为所有"模块级单例持有可变状态"的组件提供统一的 per-project 隔离方案。

设计原则：
1. 每个需要隔离的组件（如 plugin_manager / metrics_collector / tool_logger 等）
   用 PerProjectRegistry 包装，按 project 维护独立实例。
2. 兼容策略：保留原模块级入口名不变（如 plugin_manager），外部调用方无需改动。
   所有属性访问通过 __getattr__ 委托给当前 project 的实例。
3. 当前 project 来源：优先 thread-local（backend.state._current_project），
   回退到 config.project_name。

使用方式：
    # 原代码（单例）：
    plugin_manager = PluginManager()

    # 改造后（per-project 隔离）：
    from src.core.utils.per_project_registry import PerProjectRegistry
    plugin_manager = PerProjectRegistry(PluginManager)
    # 外部调用方无需改动：plugin_manager.register(...) 自动路由到当前 project 的实例
"""

import logging
from src.core.guarded import guard
import threading
from typing import Type, Any

_log = logging.getLogger(__name__)


class PerProjectRegistry:
    """通用 per-project 单例注册表。

    Args:
        factory: 工厂函数或类，用于创建新的隔离实例。调用 factory() 返回新实例。
        cleanup_attr: 可选的清理方法名，在 remove_project 时调用（如 "_release_test_accounts"）。
    """

    # ★ 内部属性集合：这些属性设置在 wrapper 自身上，不委托给底层实例。
    #   新增 wrapper 内部属性时必须同步加入此集合，否则会被错误委托。
    _INTERNAL_ATTRS = frozenset(
        {"_factory", "_cleanup_attr", "_instances", "_lock", "_default"}
    )

    def __init__(self, factory: Any, cleanup_attr: str = None):
        self._factory = factory
        self._cleanup_attr = cleanup_attr
        self._instances: dict[str, Any] = {}
        self._lock = threading.Lock()
        # 默认实例，用于无 project 上下文的兼容调用
        self._default = factory()

    def for_project(self, project: str) -> Any:
        """获取指定 project 的实例（不存在则创建）。"""
        if not project:
            return self._default
        with self._lock:
            if project not in self._instances:
                self._instances[project] = self._factory()
            return self._instances[project]

    def for_current(self) -> Any:
        """获取当前 project 的实例（从 thread-local / config 推断）。"""
        project = ""
        try:
            from src.backend.state import _current_project

            project = _current_project() or ""
        except Exception as _e:
            _log.debug(
                "[per_project_registry] _current_project 读取异常: %s",
                _e,
                exc_info=True,
            )
        if not project:
            try:
                from src.core.config_loader import config

                project = getattr(config, "project_name", "") or ""
            except Exception as _e:
                _log.debug(
                    "[per_project_registry] config.project_name 读取异常: %s",
                    _e,
                    exc_info=True,
                )
        return self.for_project(project)

    def remove_project(self, project: str):
        """移除指定 project 的实例（任务结束后清理）。"""
        with self._lock:
            instance = self._instances.pop(project, None)
        if instance and self._cleanup_attr:
            with guard("remove_project: cleanup = getattr(instance, self._cl", log=_log):
                cleanup = getattr(instance, self._cleanup_attr)
                if callable(cleanup):
                    cleanup()

    def clear_all(self):
        """清除所有 project 的实例（用于全局重置）。"""
        with self._lock:
            for project, instance in self._instances.items():
                if instance and self._cleanup_attr:
                    try:
                        cleanup = getattr(instance, self._cleanup_attr)
                        if callable(cleanup):
                            cleanup()
                    except Exception as _e:
                        _log.debug(
                            "[per_project_registry] cleanup 失败 (%s): %s",
                            project,
                            _e,
                            exc_info=True,
                        )
            self._instances.clear()

    def all_instances(self) -> list[Any]:
        """返回所有已创建的实例（包括 _default），用于全局操作（如 atexit flush）。

        ★ 用于 atexit / 全局 flush 等无 project 上下文的场景，
           确保所有 project 实例的缓冲数据都能被处理。
        """
        with self._lock:
            return [self._default] + list(self._instances.values())

    def call_all(self, method_name: str, *args, **kwargs) -> list[Any]:
        """对所有实例调用指定方法，返回结果列表。

        ★ 用于 atexit flush / 全局重置等场景。
        """
        results = []
        for instance in self.all_instances():
            with guard("call_all: method = getattr(instance, method_na", log=_log):
                method = getattr(instance, method_name)
                if callable(method):
                    results.append(method(*args, **kwargs))
        return results

    def __getattr__(self, name: str):
        """★ 对所有属性访问和方法调用，委托给当前 project 的实例。
        这确保外部调用方的 xxx.method() 都能正确路由到对应 project 的实例。"""
        return getattr(self.for_current(), name)

    def __setattr__(self, name: str, value: Any):
        """★ 对所有属性写入，委托给当前 project 的实例。
        PerProjectRegistry 自身的内部属性通过 object.__setattr__ 直接设置在 wrapper 上。
        业务属性写入委托给 for_current() 的底层实例，确保 per-project 隔离。"""
        if name in PerProjectRegistry._INTERNAL_ATTRS:
            object.__setattr__(self, name, value)
        else:
            setattr(self.for_current(), name, value)
