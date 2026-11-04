# -*- coding: utf-8 -*-
from __future__ import annotations

"""
HarnessGovernance 插件 — Agent 治理框架
========================================
解决 P0-1: Agent 频繁出错、Token 消耗失控、任务完成率低。

核心能力：
- Token 预算管控：分段预算，超限告警/熔断
- 工具精简：限制同时挂载的工具数量
- 权限管控：动作白名单 + 危险操作拦截
- 结构化反馈：强制 AI 输出结构化评估

介入点：BEFORE_ACTION / AFTER_ACTION / ON_AI_PLAN
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class HarnessGovernancePlugin:
    """Agent 治理框架插件。"""

    name = "harness_governance"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)

        # Token 预算配置
        self._token_budget_total = self.config.get(
            "token_budget_total", 100000
        )  # 总预算
        self._token_budget_per_step = self.config.get(
            "token_budget_per_step", 10000
        )  # 每步预算
        self._token_budget_warn_ratio = self.config.get(
            "token_budget_warn_ratio", 0.8
        )  # 告警阈值
        self._token_used = 0  # 已用 Token

        # 工具精简配置
        self._max_tools_per_plan = self.config.get(
            "max_tools_per_plan", 8
        )  # 单次规划最多工具数
        self._tool_cooldown_seconds = self.config.get(
            "tool_cooldown_seconds", 1
        )  # 工具调用间隔
        self._last_tool_time = 0  # 上次工具调用时间

        # 权限管控配置
        self._dangerous_actions = set(
            self.config.get(
                "dangerous_actions",
                ["delete", "remove", "drop", "truncate", "exec", "eval"],
            )
        )
        self._require_confirm_for = set(
            self.config.get(
                "require_confirm_for", ["delete_business_record", "drop_table"]
            )
        )

        # 执行统计
        self._stats = {
            "total_actions": 0,
            "token_violations": 0,
            "tool_limit_violations": 0,
            "dangerous_intercepts": 0,
            "confirm_required": 0,
        }

    def on_before_action(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        动作执行前：权限检查 + Token 预算检查。

        ctx 包含:
            action: dict — 动作定义
            engine: PlaywrightEngine
        """
        if not self.enabled:
            return None

        action = ctx.get("action", {})
        action_type = action.get("action", "").lower()

        # 1. 危险动作拦截
        if action_type in self._dangerous_actions:
            target = action.get("target_hint", "")
            _log.warning(
                "[Harness] 🚫 拦截危险动作: %s (target=%s)", action_type, target
            )
            self._stats["dangerous_intercepts"] += 1
            return {
                "harness_intercepted": True,
                "harness_reason": f"危险动作 '{action_type}' 被治理框架拦截",
                "harness_action": "block",
            }

        # 2. 需确认动作标记
        if action_type in self._require_confirm_for:
            _log.warning("[Harness] ⚠ 动作需确认: %s", action_type)
            self._stats["confirm_required"] += 1
            return {
                "harness_intercepted": False,
                "harness_confirm_required": True,
                "harness_reason": f"动作 '{action_type}' 需要人工确认",
            }

        # 3. Token 预算检查
        if self._token_used >= self._token_budget_total:
            _log.error(
                "[Harness] 💥 Token 总预算耗尽: %d / %d",
                self._token_used,
                self._token_budget_total,
            )
            self._stats["token_violations"] += 1
            return {
                "harness_intercepted": True,
                "harness_reason": f"Token 预算耗尽 ({self._token_used}/{self._token_budget_total})",
                "harness_action": "fuse",
            }

        self._stats["total_actions"] += 1
        return None

    def on_after_action(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        动作执行后：Token 消耗记录 + 预算告警。

        ctx 包含:
            action: dict
            result: bool
            token_usage: dict — {input: int, output: int}
        """
        if not self.enabled:
            return None

        token_usage = ctx.get("token_usage", {})
        input_tokens = token_usage.get("input", 0)
        output_tokens = token_usage.get("output", 0)
        total = input_tokens + output_tokens

        if total > 0:
            self._token_used += total
            ratio = self._token_used / self._token_budget_total

            if ratio >= 1.0:
                _log.error(
                    "[Harness] 💥 Token 预算已耗尽: %d / %d",
                    self._token_used,
                    self._token_budget_total,
                )
                return {"harness_token_exhausted": True}
            elif ratio >= self._token_budget_warn_ratio:
                _log.warning(
                    "[Harness] ⚠ Token 预算告警: %.1f%% (%d / %d)",
                    ratio * 100,
                    self._token_used,
                    self._token_budget_total,
                )
                return {"harness_token_warning": True, "harness_token_ratio": ratio}

        return None

    def on_ai_plan(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        AI 规划时：工具数量限制 + 结构化反馈检查。

        ctx 包含:
            goal: str — 目标
            planned_actions: list — AI 规划的操作序列
        """
        if not self.enabled:
            return None

        planned = ctx.get("planned_actions", [])

        # 1. 工具数量限制
        if len(planned) > self._max_tools_per_plan:
            _log.warning(
                "[Harness] ⚠ AI 规划工具数超限: %d > %d",
                len(planned),
                self._max_tools_per_plan,
            )
            self._stats["tool_limit_violations"] += 1
            return {
                "harness_plan_trimmed": True,
                "harness_original_count": len(planned),
                "harness_max_allowed": self._max_tools_per_plan,
                "planned_actions": planned[: self._max_tools_per_plan],
            }

        # 2. 检查是否有结构化评估
        for action in planned:
            if action.get("action") == "finish":
                evaluation = action.get("evaluation", {})
                if not evaluation or not isinstance(evaluation, dict):
                    _log.warning("[Harness] ⚠ finish 动作缺少结构化 evaluation")
                    return {
                        "harness_evaluation_missing": True,
                        "harness_reason": "AI 规划缺少结构化自评",
                    }

        return None

    def get_stats(self) -> dict[str, Any]:
        """获取治理统计。"""
        return {
            **self._stats,
            "token_used": self._token_used,
            "token_budget_total": self._token_budget_total,
            "token_remaining": self._token_budget_total - self._token_used,
            "token_usage_ratio": (
                round(self._token_used / self._token_budget_total, 4)
                if self._token_budget_total > 0
                else 0
            ),
        }

    def reset_budget(self):
        """重置 Token 预算（测试用例开始时调用）。"""
        self._token_used = 0
        self._stats = {k: 0 for k in self._stats}
        _log.info("[Harness] Token 预算已重置")
