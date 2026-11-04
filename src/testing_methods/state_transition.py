# -*- coding: utf-8 -*-
"""状态迁移法 — 通过状态迁移图分析状态之间的转换规则，验证合法迁移和非法迁移"""

from typing import Optional
from .base import BaseMethod, TestCase, MethodCategory


class StateTransition(BaseMethod):
    """状态迁移法

    分析需求中的 states 和 transitions 字段，
    为每个合法状态迁移生成正向用例，
    为每个非法状态迁移生成反向用例（被拒绝）。
    """

    method_name: str = "state_transition"
    method_category: MethodCategory = MethodCategory.BLACK_BOX
    description: str = "通过状态迁移图分析状态之间的转换规则，验证合法迁移和非法迁移"
    applicable_scenarios: list[str] = ["订单状态", "工作流", "审批流程", "游戏状态机", "登录状态"]

    # ── analyze ──────────────────────────────────────────────
    def analyze(self, requirement: dict, context: Optional[dict] = None) -> dict:
        """分析需求中的 states 和 transitions，推导合法/非法迁移

        Args:
            requirement: {
                title, description,
                states: ["idle", "loading", "loaded", ...],
                transitions: [{from, to, trigger, guard, desc}, ...],
            }

        Returns:
            {
                states: [str],
                valid_transitions: [{from, to, trigger, guard, desc}],
                invalid_transitions: [{from, to, desc}],
                state_count, transition_count,
            }
        """
        states = requirement.get("states", [])
        raw_transitions = requirement.get("transitions", [])

        # 如果没有显式 states，尝试从 transitions 推导
        if not states and raw_transitions:
            from_states = set(t.get("from", "") for t in raw_transitions)
            to_states = set(t.get("to", "") for t in raw_transitions)
            states = sorted(from_states | to_states)

        if not states:
            return {
                "states": [],
                "valid_transitions": [],
                "invalid_transitions": [],
                "state_count": 0,
                "transition_count": 0,
                "warning": "未找到状态定义",
            }

        # 规范化合法迁移
        valid_transitions = []
        valid_pairs = set()
        for i, t in enumerate(raw_transitions):
            from_state = t.get("from", "")
            to_state = t.get("to", "")
            if from_state and to_state:
                valid_transitions.append({
                    "id": t.get("id", f"T{i + 1}"),
                    "from": from_state,
                    "to": to_state,
                    "trigger": t.get("trigger", t.get("action", "")),
                    "guard": t.get("guard", t.get("condition", "")),
                    "desc": t.get("desc", t.get("description", f"{from_state} → {to_state}")),
                })
                valid_pairs.add((from_state, to_state))

        # 推导非法迁移：所有未在 valid_transitions 中的 (from, to) 对
        invalid_transitions = []
        for from_state in states:
            for to_state in states:
                if from_state == to_state:
                    continue  # 自环通常是合法的或特殊的，单独处理
                if (from_state, to_state) not in valid_pairs:
                    invalid_transitions.append({
                        "from": from_state,
                        "to": to_state,
                        "desc": f"非法迁移: {from_state} → {to_state}（应被拒绝）",
                    })

        return {
            "states": states,
            "valid_transitions": valid_transitions,
            "invalid_transitions": invalid_transitions,
            "state_count": len(states),
            "transition_count": len(valid_transitions),
            "invalid_count": len(invalid_transitions),
        }

    # ── generate_cases ───────────────────────────────────────
    def generate_cases(self, analysis: dict, filtered_taxonomy) -> list[TestCase]:
        """为每个合法迁移和非法迁移生成测试用例

        Args:
            analysis: analyze() 的返回值
            filtered_taxonomy: FilteredTaxonomy 实例

        Returns:
            list[TestCase]
        """
        valid_transitions = analysis.get("valid_transitions", [])
        invalid_transitions = analysis.get("invalid_transitions", [])

        actions = getattr(filtered_taxonomy, "actions", []) or []
        assertions = getattr(filtered_taxonomy, "assertions", []) or []

        if not actions:
            actions = ["click", "select_option", "wait"]
        if not assertions:
            assertions = ["text_contains", "element_exists", "element_enabled", "select_value"]

        cases = []
        seq = 0

        # ── 合法迁移用例 ──
        for t in valid_transitions:
            seq += 1
            case_id = f"ST-{seq:03d}"
            from_state = t["from"]
            to_state = t["to"]
            trigger = t["trigger"]
            desc = t["desc"]

            steps = self._build_steps(from_state, trigger, actions)

            case_assertions = []
            for atype in assertions:
                case_assertions.append({
                    "type": atype,
                    "target": "#app-state",
                    "desc": f"验证从 {from_state} 迁移到 {to_state}（{atype}）",
                })

            cases.append(TestCase(
                case_id=case_id,
                name=f"合法迁移: {from_state} → {to_state}",
                method=self.method_name,
                category=self.method_category.value,
                priority="high",
                module="",
                precondition=f"系统处于 {from_state} 状态",
                test_data={"from_state": from_state, "to_state": to_state, "trigger": trigger},
                steps=steps,
                assertions=case_assertions,
                coverage_dimension=f"合法状态迁移覆盖: {from_state} → {to_state}",
                tags=["state_transition", "valid", from_state, to_state],
            ))

        # ── 非法迁移用例 ──
        for t in invalid_transitions:
            seq += 1
            case_id = f"ST-{seq:03d}"
            from_state = t["from"]
            to_state = t["to"]
            desc = t["desc"]

            steps = self._build_steps(from_state, "attempt_invalid_transition", actions)

            case_assertions = []
            # 非法迁移核心断言：状态不变 + 错误提示
            case_assertions.append({
                "type": "text_contains",
                "target": "#error-message",
                "desc": f"验证系统拒绝从 {from_state} 到 {to_state} 的非法迁移",
            })
            case_assertions.append({
                "type": "element_exists",
                "target": "#app-state",
                "desc": f"验证状态仍为 {from_state}，未发生迁移",
            })
            for atype in assertions:
                if atype not in ("text_contains", "element_exists"):
                    case_assertions.append({
                        "type": atype,
                        "target": "#app-state",
                        "desc": f"验证非法迁移被拒绝后系统状态正确（{atype}）",
                    })

            cases.append(TestCase(
                case_id=case_id,
                name=f"非法迁移(应拒绝): {from_state} → {to_state}",
                method=self.method_name,
                category=self.method_category.value,
                priority="high",
                module="",
                precondition=f"系统处于 {from_state} 状态",
                test_data={"from_state": from_state, "to_state": to_state, "valid": False},
                steps=steps,
                assertions=case_assertions,
                coverage_dimension=f"非法状态迁移覆盖: {from_state} → {to_state}",
                tags=["state_transition", "invalid", from_state, to_state],
            ))

        return cases

    def _build_steps(self, current_state: str, trigger: str, actions: list[str]) -> list[dict]:
        """构建操作步骤"""
        steps = []

        # 确认当前状态
        steps.append({
            "action": "wait",
            "target": "#app-state",
            "value": current_state,
            "desc": f"确认当前状态为 {current_state}",
        })

        # 执行触发操作
        action = "click"
        if "select_option" in actions and "select" in trigger.lower():
            action = "select_option"
        elif "fill" in actions and ("input" in trigger.lower() or "enter" in trigger.lower()):
            action = "fill"

        steps.append({
            "action": action,
            "target": f"#trigger-{trigger}",
            "value": trigger,
            "desc": f"执行触发动作: {trigger}",
        })

        return steps