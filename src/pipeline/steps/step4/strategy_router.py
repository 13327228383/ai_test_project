# -*- coding: utf-8 -*-
from __future__ import annotations

"""
strategy_router.py — Step4 三策略路由选择器
============================================
根据测试用例的数据完整度（actions / checks 是否非空）自动选择执行策略，
或接受人工超驰（forced_strategy）并校验其前提条件。

三策略定义：
  策略1 — 确定性执行：actions + checks 都齐全 → 零 AI，直接执行动作 + 三轨校验
  策略2 — AI 自主探索：无 actions 有 checks → AI 拿 checks 当成功标准自主规划
  策略3 — 纯动作执行：有 actions 无 checks → 执行动作 + 基础 UI 验证
  策略0 — 跳过：actions 和 checks 都为空

纯函数，无副作用，无外部依赖（仅 typing），可独立单元测试。
"""

from typing import Any

# 策略常量
STRATEGY_DETERMINISTIC = 1  # 策略1：确定性执行（零AI）
STRATEGY_AI_EXPLORATION = 2  # 策略2：AI自主探索
STRATEGY_PURE_ACTION = 3  # 策略3：纯动作执行+基础UI验证
STRATEGY_SKIP = 0  # 策略0：跳过（数据不足）

_VALID_FORCED_STRATEGIES = (1, 2, 3)


def _select_strategy(
    actions: list[Any] | None,
    checks: list[Any] | None,
    forced_strategy: int | None = None,
) -> dict[str, Any]:
    """根据数据完整度选择执行策略，或校验人工超驰。

    参数:
        actions: 测试用例的动作列表（来自 case["actions"] 或 case_meta["actions"]）
        checks:  测试用例的检查点列表（来自 case["checks"] 或 case_meta["checks"]）
        forced_strategy: 人工指定的策略编号（1/2/3），None 表示自动选择

    返回:
        {
            "strategy": 1|2|3|0,       # 0=跳过
            "reason": "选择原因",       # 人类可读的说明
            "forced": bool,             # 是否使用了人工超驰
            "error": None|str,          # 超驰校验失败时的错误信息
        }
    """
    has_actions = bool(actions) and isinstance(actions, list) and len(actions) > 0
    has_checks = bool(checks) and isinstance(checks, list) and len(checks) > 0

    # ── 人工超驰模式 ──
    if forced_strategy is not None:
        if forced_strategy not in _VALID_FORCED_STRATEGIES:
            return {
                "strategy": STRATEGY_SKIP,
                "reason": "forced_strategy=%s 不是有效策略(1/2/3)"
                % str(forced_strategy),
                "forced": True,
                "error": "invalid_forced_strategy",
            }

        # B-4-6: 超驰前提校验
        # 策略1 需要 actions 非空（确定性执行必须有动作）
        if forced_strategy == STRATEGY_DETERMINISTIC and not has_actions:
            return {
                "strategy": STRATEGY_SKIP,
                "reason": "策略1(确定性执行)要求 actions 非空，但当前为空",
                "forced": True,
                "error": "forced_strategy_1_without_actions",
            }

        # 策略2 需要 checks 非空（AI探索必须有成功标准）
        if forced_strategy == STRATEGY_AI_EXPLORATION and not has_checks:
            return {
                "strategy": STRATEGY_SKIP,
                "reason": "策略2(AI探索)要求 checks 非空，但当前为空",
                "forced": True,
                "error": "forced_strategy_2_without_checks",
            }

        # 策略3 需要 actions 非空（纯动作执行必须有动作）
        if forced_strategy == STRATEGY_PURE_ACTION and not has_actions:
            return {
                "strategy": STRATEGY_SKIP,
                "reason": "策略3(纯动作执行)要求 actions 非空，但当前为空",
                "forced": True,
                "error": "forced_strategy_3_without_actions",
            }

        # 超驰通过校验
        _reason_map = {
            1: "人工超驰→策略1(确定性执行): %d actions + %d checks"
            % (len(actions or []), len(checks or [])),
            2: "人工超驰→策略2(AI探索): 0 actions + %d checks" % len(checks or []),
            3: "人工超驰→策略3(纯动作执行): %d actions + 0 checks" % len(actions or []),
        }
        return {
            "strategy": forced_strategy,
            "reason": _reason_map.get(forced_strategy, ""),
            "forced": True,
            "error": None,
        }

    # ── 自动选择模式 ──
    if has_actions and has_checks:
        return {
            "strategy": STRATEGY_DETERMINISTIC,
            "reason": "自动选择→策略1(确定性执行): %d actions + %d checks"
            % (len(actions), len(checks)),
            "forced": False,
            "error": None,
        }

    if not has_actions and has_checks:
        return {
            "strategy": STRATEGY_AI_EXPLORATION,
            "reason": "自动选择→策略2(AI探索): 0 actions + %d checks" % len(checks),
            "forced": False,
            "error": None,
        }

    if has_actions and not has_checks:
        return {
            "strategy": STRATEGY_PURE_ACTION,
            "reason": "自动选择→策略3(纯动作执行): %d actions + 0 checks"
            % len(actions),
            "forced": False,
            "error": None,
        }

    # 两者都空 → 跳过
    return {
        "strategy": STRATEGY_SKIP,
        "reason": "自动选择→策略0(跳过): 0 actions + 0 checks",
        "forced": False,
        "error": None,
    }
