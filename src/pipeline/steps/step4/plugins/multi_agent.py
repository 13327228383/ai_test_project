# -*- coding: utf-8 -*-
from __future__ import annotations

"""
MultiAgent 插件 — 多 Agent 协作架构
====================================
解决 P0-6: 单体 Agent 处理复杂任务时注意力衰减、工具调用逻辑坍塌。

核心能力：
- 角色分工：Planner（规划）→ Executor（执行）→ Verifier（验证）→ Reporter（报告）
- 信息截断：Agent 间只传递核心变量（JSON），不传完整历史
- 任务分解：复杂意图拆分为子任务，分配给不同 Agent
- 结果聚合：各 Agent 结果汇总为最终输出

介入点：ON_AI_PLAN / BEFORE_STEP / AFTER_STEP
"""

import logging
from typing import Any
from enum import Enum

_log = logging.getLogger(__name__)


class AgentRole(str, Enum):
    """Agent 角色定义。"""

    PLANNER = "planner"  # 规划者：分解任务、制定策略
    EXECUTOR = "executor"  # 执行者：执行具体操作
    VERIFIER = "verifier"  # 验证者：检查结果正确性
    REPORTER = "reporter"  # 报告者：生成执行报告
    HEALER = "healer"  # 修复者：处理失败和异常


class MultiAgentPlugin:
    """多 Agent 协作插件。"""

    name = "multi_agent"
    enabled = True
    config = {}

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)

        # 角色配置
        self._roles_enabled = self.config.get(
            "roles_enabled", [AgentRole.PLANNER, AgentRole.EXECUTOR, AgentRole.VERIFIER]
        )
        self._max_subtasks = self.config.get("max_subtasks", 5)

        # 任务状态
        self._active_tasks: dict[str, dict] = {}  # task_id → task_state
        self._agent_outputs: dict[str, dict] = {}  # task_id → {role: output}

        # 统计
        self._stats = {
            "tasks_decomposed": 0,
            "subtasks_created": 0,
            "agent_handoffs": 0,
            "verification_failures": 0,
        }

    def on_ai_plan(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        AI 规划时：使用 Planner Agent 分解任务。

        ctx 包含:
            goal: str — 原始目标
            planned_actions: list — 初步规划的操作序列
        """
        if not self.enabled or AgentRole.PLANNER not in self._roles_enabled:
            return None

        goal = ctx.get("goal", "")
        planned = ctx.get("planned_actions", [])

        if len(planned) <= 3:
            # 简单任务不需要多 Agent
            return None

        self._stats["tasks_decomposed"] += 1

        # 模拟 Planner Agent 分解任务
        subtasks = self._decompose_task(goal, planned)
        self._stats["subtasks_created"] += len(subtasks)

        _log.info("[MultiAgent] Planner 分解任务: %d 个子任务", len(subtasks))

        return {
            "multi_agent_decomposed": True,
            "subtasks": subtasks,
            "original_plan": planned,
        }

    def on_before_step(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        步骤开始前：Executor Agent 准备执行。

        ctx 包含:
            step: dict
            subtask_id: str — 子任务 ID（如果有）
        """
        if not self.enabled or AgentRole.EXECUTOR not in self._roles_enabled:
            return None

        step = ctx.get("step", {})
        subtask_id = ctx.get("subtask_id", "")

        # 构建 Executor 的精简上下文（信息截断）
        # ★ B3 单一性：输出 key 统一为 target_hint（与 step 字段命名一致）
        executor_context = {
            "action": step.get("action", ""),
            "target_hint": step.get("target_hint", ""),
            "expected": step.get("expected", ""),
            # 不传递完整历史，只传递关键状态
            "key_state": ctx.get("key_state", {}),
        }

        self._stats["agent_handoffs"] += 1

        return {
            "multi_agent_executor_context": executor_context,
            "multi_agent_role": AgentRole.EXECUTOR.value,
        }

    def on_after_step(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        """
        步骤完成后：Verifier Agent 检查结果。

        ctx 包含:
            step: dict
            result: bool
            step_result_entry: dict
        """
        if not self.enabled or AgentRole.VERIFIER not in self._roles_enabled:
            return None

        step_result = ctx.get("step_result_entry", {})
        if not step_result:
            return None

        # 模拟 Verifier Agent 检查
        verification = self._verify_result(step_result)

        if not verification.get("passed", True):
            self._stats["verification_failures"] += 1
            _log.warning(
                "[MultiAgent] Verifier 检查未通过: %s", verification.get("reason", "?")
            )

        return {
            "multi_agent_verified": verification.get("passed", True),
            "multi_agent_verification": verification,
            "multi_agent_role": AgentRole.VERIFIER.value,
        }

    def _decompose_task(self, goal: str, planned_actions: list[dict]) -> list[dict]:
        """Planner Agent：将复杂任务分解为子任务。"""
        subtasks = []

        # 按操作类型分组
        current_group = []
        current_type = ""

        for action in planned_actions:
            action_type = action.get("action", "").lower()

            # 导航操作单独成组
            if action_type in ["navigate", "goto", "back"]:
                if current_group:
                    subtasks.append(
                        {
                            "id": f"subtask-{len(subtasks)}",
                            "type": current_type or "action_group",
                            "actions": current_group,
                            "role": AgentRole.EXECUTOR.value,
                        }
                    )
                    current_group = []
                subtasks.append(
                    {
                        "id": f"subtask-{len(subtasks)}",
                        "type": "navigation",
                        "actions": [action],
                        "role": AgentRole.EXECUTOR.value,
                    }
                )
                continue

            # 验证操作分配给 Verifier
            if action_type in ["verify", "assert", "check"]:
                if current_group:
                    subtasks.append(
                        {
                            "id": f"subtask-{len(subtasks)}",
                            "type": current_type or "action_group",
                            "actions": current_group,
                            "role": AgentRole.EXECUTOR.value,
                        }
                    )
                    current_group = []
                subtasks.append(
                    {
                        "id": f"subtask-{len(subtasks)}",
                        "type": "verification",
                        "actions": [action],
                        "role": AgentRole.VERIFIER.value,
                    }
                )
                continue

            # 普通操作分组
            if not current_type:
                current_type = action_type
            current_group.append(action)

        # 最后一组
        if current_group:
            subtasks.append(
                {
                    "id": f"subtask-{len(subtasks)}",
                    "type": current_type,
                    "actions": current_group,
                    "role": AgentRole.EXECUTOR.value,
                }
            )

        # 限制子任务数量
        if len(subtasks) > self._max_subtasks:
            # 合并相邻的同类型子任务
            merged = []
            for st in subtasks:
                if (
                    merged
                    and merged[-1]["type"] == st["type"]
                    and merged[-1]["role"] == st["role"]
                ):
                    merged[-1]["actions"].extend(st["actions"])
                else:
                    merged.append(dict(st))
            subtasks = merged[: self._max_subtasks]

        return subtasks

    def _verify_result(self, step_result: dict) -> dict[str, Any]:
        """Verifier Agent：检查结果。"""
        issues = []

        # 1. 检查是否通过
        if not step_result.get("passed", False):
            issues.append("步骤执行失败")

        # 2. 检查重试次数
        retries = step_result.get("rounds_needed", 1) - 1
        if retries > 3:
            issues.append(f"重试次数过多 ({retries} 次)")

        # 3. 检查执行耗时
        elapsed = step_result.get("elapsed_ms", 0) or step_result.get("elapsed", 0)
        if elapsed > 30000:
            issues.append(f"执行耗时过长 ({elapsed}ms)")

        # 4. 检查是否有验证
        has_verification = bool(
            step_result.get("db_verification")
            or step_result.get("api_verification")
            or step_result.get("screenshot_verified")
        )
        if not has_verification:
            issues.append("缺少验证手段")

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "reason": "; ".join(issues) if issues else "通过",
        }

    def get_stats(self) -> dict[str, Any]:
        """获取多 Agent 统计。"""
        return {
            **self._stats,
            "roles_enabled": [r.value for r in self._roles_enabled],
            "active_tasks": len(self._active_tasks),
        }

    def get_task_state(self, task_id: str) -> dict | None:
        """获取任务状态。"""
        return self._active_tasks.get(task_id)

    def reset(self):
        """重置状态。"""
        self._active_tasks.clear()
        self._agent_outputs.clear()
        self._stats = {k: 0 for k in self._stats}
