# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Step2 需求分析策略插件
======================
支持动态注册需求分析策略，如领域特定模式识别、合规性检查。

介入点：STEP2_START（注入策略）
"""

import logging
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)


class Step2AnalyzerPlugin:
    """Step2 需求分析策略插件。"""

    name = "step2_analyzer"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        # 分析策略注册表
        self._strategies: dict[str, dict] = {}
        # 领域关键词映射（用于导航地图生成）
        self._domain_keywords: dict[str, list[str]] = {}

    def register_strategy(
        self,
        name: str,
        analyze_func: Callable,
        applies_to: list[str] = None,
        priority: int = 50,
    ):
        """
        注册分析策略。

        参数：
            name: 策略标识
            analyze_func: 分析函数，签名 analyze_func(requirements, context) → analysis_result
            applies_to: 适用的模块列表（None=全部）
            priority: 优先级（越小越先执行）
        """
        self._strategies[name] = {
            "func": analyze_func,
            "applies_to": applies_to or [],
            "priority": priority,
        }
        _log.info("[Step2Analyzer] 注册策略: %s (priority=%d)", name, priority)

    def register_domain_keywords(self, domain: str, keywords: list[str]):
        """
        注册领域关键词（用于系统导航地图生成）。

        参数：
            domain: 领域标识（如 "finance", "healthcare", "iot"）
            keywords: 模块关键词列表
        """
        self._domain_keywords[domain] = keywords
        _log.info("[Step2Analyzer] 注册领域关键词: %s (%d 个)", domain, len(keywords))

    def on_step2_start(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        Step2 开始时，注入分析策略和领域关键词。

        ctx 包含:
            project: str
            requirements: list — 需求列表
            strategies: dict — 当前策略列表
            domain_keywords: dict — 当前领域关键词
        """
        if not self.enabled:
            return None

        result = {}

        # 注入策略
        strategies = ctx.get("strategies", {})
        for name, strategy in self._strategies.items():
            if name not in strategies:
                strategies[name] = strategy
        if strategies:
            result["strategies"] = strategies

        # 注入领域关键词
        domain_keywords = ctx.get("domain_keywords", {})
        for domain, keywords in self._domain_keywords.items():
            if domain not in domain_keywords:
                domain_keywords[domain] = keywords
        if domain_keywords:
            result["domain_keywords"] = domain_keywords

        if result:
            _log.info(
                "[Step2Analyzer] 注入 %d 个策略, %d 个领域",
                len(strategies),
                len(domain_keywords),
            )
        return result if result else None

    def on_step2_complete(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        Step2 完成后，记录分析质量统计。

        ctx 包含:
            project: str
            requirements: list
            analysis_results: dict
        """
        if not self.enabled:
            return None

        reqs = ctx.get("requirements", [])
        results = ctx.get("analysis_results", {})

        stats = {
            "total_requirements": len(reqs),
            "strategies_applied": list(results.keys()),
            "issues_found": sum(len(r.get("issues", [])) for r in results.values()),
        }

        _log.info("[Step2Analyzer] 分析统计: %s", stats)
        return {"analysis_stats": stats}

    def list_strategies(self) -> dict[str, dict]:
        """列出所有已注册的策略。"""
        return {
            name: {"priority": s["priority"], "applies_to": s["applies_to"]}
            for name, s in self._strategies.items()
        }

    def list_domains(self) -> dict[str, list[str]]:
        """列出所有已注册的领域。"""
        return dict(self._domain_keywords)
