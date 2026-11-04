# -*- coding: utf-8 -*-
"""数据源注册表 — 通用数据源管理，支持热插拔。

★ 通用性修复:
  将 Step1 中硬编码的 4 个数据源（Confluence/Figma/DB/YApi）抽象为注册表模式。
  新增数据源只需注册即可，无需修改 Step1 核心代码。

使用方式:
    from src.pipeline.datasource_registry import registry
    registry.register("jira", fetch_jira, schema={"url": "str", "token": "str"})
    sources = registry.enabled_sources(project_config)
    results = registry.fetch_all(sources, project, version_label)
"""

from __future__ import annotations

import logging
from collections.abc import Callable

# 引擎内置数据源 key 唯一事实 —— step1 阻断判定与 fetchers 兜底均从此派生
BUILTIN_SOURCE_KEYS = ("confluence", "figma", "yapi", "database")
from typing import Any

_log = logging.getLogger(__name__)


class DataSourceRegistry:
    """数据源注册表 — 管理所有数据源的注册、发现和并行执行。"""

    def __init__(self):
        self._sources: dict[str, dict] = {}

    def register(
        self,
        name: str,
        fetch_func: Callable,
        config_schema: dict[str, str] | None = None,
        timeout_seconds: int = 60,
        group: str = "external",
    ) -> None:
        """注册数据源。

        Args:
            name: 数据源标识（如 "confluence", "figma", "jira", "notion"）
            fetch_func: 拉取函数，签名: fetch_func(fetcher, version_label, **kwargs) → result
            config_schema: 配置项 Schema（供前端展示表单），如 {"url": "str", "token": "str"}
            timeout_seconds: 拉取超时时间（秒）
            group: 分组标签（external / internal / custom）
        """
        self._sources[name] = {
            "fetch": fetch_func,
            "schema": config_schema or {},
            "timeout": timeout_seconds,
            "group": group,
        }
        _log.info("[DataSourceRegistry] 注册: %s (timeout=%ds, group=%s)", name, timeout_seconds, group)

    def unregister(self, name: str) -> None:
        """注销数据源。"""
        self._sources.pop(name, None)
        _log.info("[DataSourceRegistry] 注销: %s", name)

    def get(self, name: str) -> dict | None:
        """获取数据源配置。"""
        return self._sources.get(name)

    def list_all(self) -> dict[str, dict]:
        """列出所有已注册的数据源。"""
        return {
            name: {"schema": ds["schema"], "timeout": ds["timeout"], "group": ds["group"]}
            for name, ds in self._sources.items()
        }

    def enabled_sources(self, skip_sources: list[str] | None = None) -> dict[str, dict]:
        """获取所有已启用（未被跳过）的数据源。

        Args:
            skip_sources: 用户选择跳过的数据源列表

        Returns:
            {name: {fetch, timeout, ...}, ...}
        """
        skip = set(skip_sources or [])
        return {
            name: ds
            for name, ds in self._sources.items()
            if name not in skip
        }

    def source_keys(self) -> list[str]:
        """获取所有已注册数据源的 key 列表。"""
        return list(self._sources.keys())


# 全局单例
registry = DataSourceRegistry()


# ═══════════════════════════════════════════════════════════
# 内置数据源注册（在模块加载时自动注册）
# ═══════════════════════════════════════════════════════════

def _register_builtin_sources():
    """注册内置的 4 个数据源。"""
    # 延迟导入，避免循环依赖
    from src.pipeline._step1_helpers import (
        _fetch_confluence_in_thread,
        _fetch_figma_in_thread,
        _fetch_database_in_thread,
        _fetch_yapi_in_thread,
    )

    registry.register(
        "confluence",
        _fetch_confluence_in_thread,
        config_schema={"page_ids": "str", "mode": "file|mcp|rest"},
        timeout_seconds=300,
        group="external",
    )
    registry.register(
        "figma",
        _fetch_figma_in_thread,
        config_schema={"url": "str", "key": "str"},
        timeout_seconds=60,
        group="external",
    )
    registry.register(
        "database",
        _fetch_database_in_thread,
        config_schema={"host": "str", "user": "str", "password": "str", "name": "str"},
        timeout_seconds=60,
        group="external",
    )
    registry.register(
        "yapi",
        _fetch_yapi_in_thread,
        config_schema={"base_url": "str", "username": "str", "password": "str"},
        timeout_seconds=300,
        group="external",
    )
    _log.info("[DataSourceRegistry] 内置数据源注册完成: %s", registry.source_keys())


_register_builtin_sources()