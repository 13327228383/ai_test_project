# -*- coding: utf-8 -*-
"""执行器双路径收敛（ExecutionRouter）—— 流水线强化专项 T6。

★ 职责：以**纯函数**固化双路径路由规则，消除「AgentExplorer 实时探索」与
  「经验/探针 step loop」两条执行路径的行为漂移风险（design §1.2 #5 / PRD Q3）：

    有 `_explore_task`（非空）→ 走 AgentExplorer 实时探索路径
    无 `_explore_task`         → 走经验/探针 step loop 路径

★ 设计约束（Q3 拍板：不二选一）：
  - `plan.execute(plan)` 仍是唯一门面；本模块只负责**决策 + 描述**，
    不复制任何执行逻辑、不重复执行（同一用例单次执行只走一条路径）。
  - 纯函数：无 IO、无 DB、无 AI 调用，可独立单测。
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

# ── 路由常量（唯一事实源）──
PATH_AGENT_EXPLORER = "agent_explorer"
PATH_EMPIRICAL_STEP_LOOP = "empirical_step_loop"


class ExecutionRouter:
    """执行器双路径路由器（纯函数，无状态）。"""

    @staticmethod
    def route_case(case: dict[str, Any]) -> str:
        """对单条用例做确定性路由决策。

        路由规则（design §1.2 #5 / PRD Q3）：
          - 用例含 `_explore_task` 且非空 → ``agent_explorer``
          - 否则 → ``empirical_step_loop``

        Args:
            case: 用例 dict（可能含 _explore_task / explore_task 字段）。

        Returns:
            路径常量：PATH_AGENT_EXPLORER 或 PATH_EMPIRICAL_STEP_LOOP。
        """
        if not isinstance(case, dict):
            return PATH_EMPIRICAL_STEP_LOOP
        explore = case.get("_explore_task")
        if explore is None:
            explore = case.get("explore_task")
        if isinstance(explore, dict) and explore:
            return PATH_AGENT_EXPLORER
        if isinstance(explore, str) and explore.strip():
            return PATH_AGENT_EXPLORER
        return PATH_EMPIRICAL_STEP_LOOP

    @classmethod
    def describe(cls, plan: Any) -> dict[str, Any]:
        """描述一份验证计划的整体路由分布（供 plan.execute 记录决策日志）。

        Args:
            plan: VerificationPlan（含 cases 列表）。

        Returns:
            {
              "total_cases": int,
              "agent_explorer": int,       # 走探索路径的用例数
              "empirical_step_loop": int,  # 走经验/探针路径的用例数
              "by_case": {case_id: path},  # 逐用例路由（无 case_id 用索引）
            }
        """
        cases = getattr(plan, "cases", None) or []
        by_case: dict[str, str] = {}
        agent = 0
        empirical = 0
        for idx, case in enumerate(cases):
            case_id = str(case.get("case_id") or f"case-{idx}") if isinstance(case, dict) else f"case-{idx}"
            path = cls.route_case(case)
            by_case[case_id] = path
            if path == PATH_AGENT_EXPLORER:
                agent += 1
            else:
                empirical += 1
        return {
            "total_cases": len(cases),
            "agent_explorer": agent,
            "empirical_step_loop": empirical,
            "by_case": by_case,
        }

    @staticmethod
    def route_rules_markdown() -> str:
        """返回路由规则说明（供执行配置页文案/文档复用）。"""
        return (
            "双路径路由规则（T6 流水线强化专项，确定性路由、不二选一）：\n"
            "- 用例含 `_explore_task`（非空）→ AgentExplorer 实时探索路径\n"
            "- 用例无 `_explore_task` → 经验/探针 step loop 路径\n"
            "- 同一用例单次执行只走一条路径（无重复执行）；`execute(plan)` 为唯一门面。"
        )
