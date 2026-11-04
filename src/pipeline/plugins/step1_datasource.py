# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Step1 数据源注册插件
====================
支持动态注册新数据源，无需修改 Step1 核心代码。

介入点：STEP1_START（注册数据源）
"""

import logging
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)


class Step1DatasourcePlugin:
    """Step1 数据源注册插件。"""

    name = "step1_datasource"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        # 数据源注册表: name → fetch_function
        self._datasources: dict[str, Callable] = {}

    def register_datasource(
        self, name: str, fetch_func: Callable, config_schema: dict = None
    ):
        """
        注册新数据源。

        参数：
            name: 数据源标识（如 "jira", "notion", "swagger"）
            fetch_func: 拉取函数，签名 fetch_func(project, config) → data_dict
            config_schema: 配置项 Schema（供前端展示表单）
        """
        self._datasources[name] = {
            "fetch": fetch_func,
            "schema": config_schema or {},
        }
        _log.info("[Step1Datasource] 注册数据源: %s", name)

    def on_step1_start(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        Step1 开始时，注入已注册的数据源。

        ctx 包含:
            project: str — 项目名
            config: dict — Step1 配置
            datasources: dict — 当前数据源列表（可被扩展）
        """
        if not self.enabled:
            return None

        datasources = ctx.get("datasources", {})
        for name, ds in self._datasources.items():
            if name not in datasources:
                datasources[name] = ds
                _log.info("[Step1Datasource] 注入数据源: %s", name)

        return {"datasources": datasources}

    def on_step1_complete(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        Step1 完成后，记录各数据源拉取结果统计。

        ctx 包含:
            project: str
            results: dict — 各数据源拉取结果
        """
        if not self.enabled:
            return None

        results = ctx.get("results", {})
        stats = {}
        for name, result in results.items():
            stats[name] = {
                "success": result.get("success", False),
                "item_count": len(result.get("items", [])),
                "elapsed_ms": result.get("elapsed_ms", 0),
            }

        _log.info("[Step1Datasource] 拉取统计: %s", stats)
        return {"datasource_stats": stats}

    def list_datasources(self) -> dict[str, dict]:
        """列出所有已注册的数据源。"""
        return {
            name: {"schema": ds["schema"]} for name, ds in self._datasources.items()
        }
