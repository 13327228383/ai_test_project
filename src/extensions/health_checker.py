# -*- coding: utf-8 -*-
"""
外部依赖健康检查器 (HealthChecker)
====================================
轻量级、非阻塞地检查所有外部依赖状态。
每个检查返回统一结构：{name, status, latency_ms, error_msg}。

支持的依赖：
  - DeepSeek / OpenAI / Anthropic API
  - Confluence 服务器
  - Figma API
  - YApi 服务器
  - MySQL 连接
  - 磁盘空间
  - 内存使用
"""

import time

from .health_checker_mixins._base import HealthCheckerBaseMixin
from .health_checker_mixins._ai import HealthCheckerAiMixin
from .health_checker_mixins._external import HealthCheckerExternalMixin
from .health_checker_mixins._system import HealthCheckerSystemMixin


class HealthChecker(
    HealthCheckerBaseMixin,
    HealthCheckerAiMixin,
    HealthCheckerExternalMixin,
    HealthCheckerSystemMixin,
):
    """外部依赖健康检查器。线程安全，支持并发检查。"""

    # 检查超时（秒）
    TIMEOUT = 10

    # ═══════════════════════════════════════════════════════════════
    # 批量检查
    # ═══════════════════════════════════════════════════════════════

    def run_all_checks(self, use_cache: bool = True) -> list[dict]:
        """
        运行所有外部依赖健康检查。

        参数:
            use_cache: 是否使用 30 秒缓存（默认 True）。

        返回:
            每个依赖的检查结果列表，按固定顺序排列。
        """
        if use_cache:
            cached = self._get_cached()
            if cached:
                return cached

        checks = [
            ("ai_api_deepseek", lambda: self.check_ai_api("deepseek")),
            ("ai_api_openai", lambda: self.check_ai_api("openai")),
            ("ai_api_anthropic", lambda: self.check_ai_api("anthropic")),
            ("confluence", self.check_confluence),
            ("figma", self.check_figma),
            ("yapi", self.check_yapi),
            ("mysql", self.check_mysql),
            ("disk", self.check_disk),
            ("memory", self.check_memory),
        ]

        return self._check_all(checks)

    def get_summary(self) -> dict:
        """返回健康检查摘要（用于 SSE 推送）。"""
        checks = self.run_all_checks(use_cache=True)
        total = len(checks)
        up = sum(1 for c in checks if c["status"] == "up")
        down = total - up
        return {
            "timestamp": time.time(),
            "total": total,
            "up": up,
            "down": down,
            "checks": checks,
        }


# 全局单例
# ★ 方案C 改造：health_checker 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

health_checker = PerProjectRegistry(HealthChecker)
