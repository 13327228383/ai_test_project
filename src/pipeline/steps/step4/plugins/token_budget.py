# -*- coding: utf-8 -*-
from __future__ import annotations

"""
TokenBudget 插件 — 上下文 Token 预算管控
=========================================
解决 P0-4: 上下文膨胀导致 Agent "失忆"、死循环。

核心能力：
- 分段 Token 预算：系统指令 / 历史记录 / 当前任务 / 工具输出 分别管控
- 上下文截断：超长时自动截断历史，保留关键信息
- 信息瘦身：去除冗余，压缩重复内容
- 预算告警：接近上限时提醒 AI 精简输出

介入点：BEFORE_STEP / ON_AI_PLAN
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class TokenBudgetPlugin:
    """Token 预算管控插件。"""

    name = "token_budget"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)

        # 分段预算配置（总计约 8K tokens，适配常见模型上下文窗口）
        self._budgets = self.config.get(
            "budgets",
            {
                "system_prompt": 1500,  # 系统指令
                "history": 3000,  # 历史记录
                "current_task": 2000,  # 当前任务描述
                "tool_output": 1500,  # 工具输出
                "reserve": 1000,  # 预留（AI 输出 + 缓冲）
            },
        )
        self._total_budget = sum(self._budgets.values())

        # 截断策略
        self._history_max_items = self.config.get("history_max_items", 10)
        self._history_compress_ratio = self.config.get("history_compress_ratio", 0.5)
        self._dom_max_chars = self.config.get("dom_max_chars", 3000)

        # 统计
        self._stats = {
            "total_steps": 0,
            "truncations": 0,
            "compressions": 0,
            "budget_warnings": 0,
            "budget_exceeded": 0,
        }

    def on_before_step(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        步骤开始前：检查并截断上下文。

        ctx 包含:
            step: dict
            action_history: list — 历史动作记录
            memory_context: str — 记忆上下文
            page_state: dict — 页面状态
        """
        if not self.enabled:
            return None

        self._stats["total_steps"] += 1

        # 1. 估算当前上下文 Token 数
        action_history = ctx.get("action_history", [])
        memory_context = ctx.get("memory_context", "")
        page_state = ctx.get("page_state", {})

        estimated_tokens = self._estimate_tokens(
            action_history, memory_context, page_state
        )

        # 2. 如果超过预算，进行截断
        if estimated_tokens > self._total_budget:
            self._stats["budget_exceeded"] += 1
            _log.warning(
                "[TokenBudget] 💥 上下文超限: %d > %d tokens",
                estimated_tokens,
                self._total_budget,
            )

            # 截断历史
            truncated_history = self._truncate_history(action_history)
            # 压缩页面状态
            compressed_state = self._compress_page_state(page_state)
            # 压缩记忆
            compressed_memory = self._compress_memory(memory_context)

            new_estimate = self._estimate_tokens(
                truncated_history, compressed_memory, compressed_state
            )
            _log.info(
                "[TokenBudget] 截断后: %d → %d tokens", estimated_tokens, new_estimate
            )

            return {
                "token_budget_truncated": True,
                "token_original": estimated_tokens,
                "token_after": new_estimate,
                "action_history": truncated_history,
                "memory_context": compressed_memory,
                "page_state": compressed_state,
            }

        # 3. 如果接近预算，告警
        warning_ratio = 0.85
        if estimated_tokens > self._total_budget * warning_ratio:
            self._stats["budget_warnings"] += 1
            _log.warning(
                "[TokenBudget] ⚠ 上下文接近上限: %d / %d (%.1f%%)",
                estimated_tokens,
                self._total_budget,
                estimated_tokens / self._total_budget * 100,
            )
            return {
                "token_budget_warning": True,
                "token_usage": estimated_tokens,
                "token_budget": self._total_budget,
                "token_usage_ratio": round(estimated_tokens / self._total_budget, 4),
            }

        return None

    def on_ai_plan(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        AI 规划时：检查 planned_actions 的 Token 消耗。

        ctx 包含:
            goal: str
            planned_actions: list
        """
        if not self.enabled:
            return None

        planned = ctx.get("planned_actions", [])
        if not planned:
            return None

        # 估算 planned_actions 的 Token 数
        planned_json = str(planned)
        estimated = len(planned_json) // 3  # 粗略估算：1 token ≈ 3 字符

        if estimated > self._budgets.get("tool_output", 1500):
            self._stats["truncations"] += 1
            _log.warning("[TokenBudget] ⚠ AI 规划输出过长: %d tokens", estimated)
            return {
                "token_plan_too_long": True,
                "token_plan_estimate": estimated,
                "token_plan_budget": self._budgets.get("tool_output"),
            }

        return None

    def _estimate_tokens(self, history: list, memory: str, page_state: dict) -> int:
        """估算上下文 Token 数（粗略估算）。"""
        total = 0

        # 历史记录
        history_str = str(history)
        total += len(history_str) // 3

        # 记忆上下文
        total += len(memory) // 3

        # 页面状态
        state_str = str(page_state)
        total += len(state_str) // 3

        return total

    def _truncate_history(self, history: list) -> list:
        """截断历史记录，保留最近 N 条 + 关键步骤。"""
        if len(history) <= self._history_max_items:
            return history

        self._stats["truncations"] += 1

        # 保留最近 N 条
        recent = history[-self._history_max_items :]

        # 压缩更早的历史为摘要
        older = history[: -self._history_max_items]
        if older:
            summary = f"[前 {len(older)} 步已压缩] 关键操作: "
            key_actions = []
            for item in older:
                if isinstance(item, dict):
                    action = item.get("action", "")
                    if action and action not in key_actions:
                        key_actions.append(action)
            summary += ", ".join(key_actions[:5])
            return [{"_compressed_summary": summary}] + recent

        return recent

    def _compress_page_state(self, page_state: dict) -> dict:
        """压缩页面状态，保留关键信息。"""
        if not page_state:
            return page_state

        self._stats["compressions"] += 1

        compressed = dict(page_state)

        # 截断过长的文本内容
        if "text" in compressed and len(compressed["text"]) > self._dom_max_chars:
            compressed["text"] = (
                compressed["text"][: self._dom_max_chars] + "... [截断]"
            )

        # 精简元素列表（只保留可交互元素）
        if "elements" in compressed and isinstance(compressed["elements"], list):
            interactive = [e for e in compressed["elements"] if e.get("interactive")]
            if len(interactive) < len(compressed["elements"]):
                compressed["elements"] = interactive
                compressed["_compressed"] = True

        return compressed

    def _compress_memory(self, memory: str) -> str:
        """压缩记忆上下文。"""
        if not memory or len(memory) <= self._budgets.get("current_task", 2000):
            return memory

        self._stats["compressions"] += 1

        # 保留前 30% 和后 70%（通常开头有关键事实，结尾有最近操作）
        limit = self._budgets.get("current_task", 2000) * 3  # 字符数
        if len(memory) > limit:
            head_len = int(limit * 0.3)
            tail_len = int(limit * 0.7)
            return (
                memory[:head_len] + "\n... [中间内容已压缩] ...\n" + memory[-tail_len:]
            )

        return memory

    def get_stats(self) -> dict[str, Any]:
        """获取 Token 预算统计。"""
        return {
            **self._stats,
            "total_budget": self._total_budget,
            "budget_breakdown": self._budgets,
        }

    def get_budget_status(self) -> dict[str, Any]:
        """获取当前预算状态。"""
        return {
            "total_budget": self._total_budget,
            "budgets": self._budgets,
            "stats": self._stats,
        }
