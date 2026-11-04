# -*- coding: utf-8 -*-
"""判定表法 — 黑盒测试方法

列出所有条件桩和动作桩，穷举条件组合形成判定表，每个规则生成一个测试用例。
"""

from typing import Optional
from .base import BaseMethod, TestCase, MethodCategory


class DecisionTable(BaseMethod):
    method_name: str = "decision_table"
    method_category: MethodCategory = MethodCategory.BLACK_BOX
    description: str = "列出所有条件桩和动作桩，穷举条件组合形成判定表，每个规则生成一个测试用例"
    applicable_scenarios: list[str] = ["多条件组合", "业务规则", "决策逻辑", "审批流程", "权限矩阵"]

    def analyze(self, requirement: dict, context: Optional[dict] = None) -> dict:
        """分析需求中的多个条件及其组合，构建判定表。

        Args:
            requirement: {
                title, description,
                conditions: [
                    {name, field, type: "boolean"|"range"|"enum", values: [True, False] | [...] },
                ],
                actions: [
                    {name, desc, field, result},
                ],
                rules: [
                    {condition_values: {cond_name: True/False}, action_values: {action_name: True/False}},
                ],
            }
            context: 额外上下文

        Returns:
            {
                "condition_stubs": [{name, field, type, values}],
                "action_stubs": [{name, desc, field, result}],
                "decision_table": {
                    "headers": ["cond1", "cond2", ..., "act1", "act2", ...],
                    "rules": [{condition_values: [...], action_values: [...], simplified: bool}],
                },
                "summary": "..."
            }
        """
        conditions = requirement.get("conditions", [])
        actions = requirement.get("actions", [])
        explicit_rules = requirement.get("rules", [])

        # 提取条件桩
        condition_stubs = []
        for i, cond in enumerate(conditions):
            stub = {
                "index": i,
                "name": cond.get("name", f"条件{i + 1}"),
                "field": cond.get("field", ""),
                "type": cond.get("type", "boolean"),
                "values": cond.get("values", [True, False]),
            }
            condition_stubs.append(stub)

        # 提取动作桩
        action_stubs = []
        for i, act in enumerate(actions):
            stub = {
                "index": i,
                "name": act.get("name", f"动作{i + 1}"),
                "desc": act.get("desc", ""),
                "field": act.get("field", ""),
                "result": act.get("result", None),
            }
            action_stubs.append(stub)

        # 如果没有显式 conditions，从 fields 推断
        if not condition_stubs:
            condition_stubs, action_stubs = self._infer_from_requirement(requirement)

        # 构建判定表
        if explicit_rules:
            decision_table = self._build_from_explicit_rules(condition_stubs, action_stubs, explicit_rules)
        else:
            decision_table = self._build_full_decision_table(condition_stubs, action_stubs)

        return {
            "condition_stubs": condition_stubs,
            "action_stubs": action_stubs,
            "decision_table": decision_table,
            "total_conditions": len(condition_stubs),
            "total_actions": len(action_stubs),
            "total_rules": len(decision_table.get("rules", [])),
            "summary": f"共 {len(condition_stubs)} 个条件桩，{len(action_stubs)} 个动作桩，{len(decision_table.get('rules', []))} 条规则",
        }

    def _infer_from_requirement(self, requirement: dict) -> tuple:
        """从需求描述中推断条件和动作。"""
        condition_stubs = []
        action_stubs = []

        fields = requirement.get("fields", [])
        description = requirement.get("description", "")
        title = requirement.get("title", "")

        # 从 fields 推断条件
        for i, field in enumerate(fields):
            field_name = field.get("name", "")
            field_type = field.get("type", "text").lower()
            constraints = field.get("constraints", {})
            required = field.get("required", False)

            if required:
                condition_stubs.append({
                    "index": len(condition_stubs),
                    "name": f"{field_name}已填写",
                    "field": field_name,
                    "type": "boolean",
                    "values": [True, False],
                })

            if field_type in ("select", "dropdown", "enum") and constraints.get("options"):
                condition_stubs.append({
                    "index": len(condition_stubs),
                    "name": f"{field_name}选项",
                    "field": field_name,
                    "type": "enum",
                    "values": constraints["options"],
                })

            if constraints.get("min") is not None or constraints.get("max") is not None:
                condition_stubs.append({
                    "index": len(condition_stubs),
                    "name": f"{field_name}在范围内",
                    "field": field_name,
                    "type": "boolean",
                    "values": [True, False],
                })

        # 推断动作
        action_stubs.append({
            "index": 0,
            "name": "提交成功",
            "desc": "表单提交成功，显示成功提示",
            "field": "form",
            "result": "success",
        })
        action_stubs.append({
            "index": 1,
            "name": "显示错误",
            "desc": "显示验证错误提示",
            "field": "form",
            "result": "error",
        })

        return condition_stubs, action_stubs

    def _build_from_explicit_rules(self, condition_stubs: list[dict],
                                    action_stubs: list[dict],
                                    explicit_rules: list[dict]) -> dict:
        """根据显式定义的规则构建判定表。"""
        headers = [c["name"] for c in condition_stubs] + [a["name"] for a in action_stubs]
        rules = []

        for rule in explicit_rules:
            condition_values = []
            for cond in condition_stubs:
                cond_name = cond["name"]
                val = rule.get("condition_values", {}).get(cond_name, False)
                condition_values.append(val)

            action_values = []
            for act in action_stubs:
                act_name = act["name"]
                val = rule.get("action_values", {}).get(act_name, False)
                action_values.append(val)

            rules.append({
                "condition_values": condition_values,
                "action_values": action_values,
                "simplified": False,
            })

        return {"headers": headers, "rules": rules}

    def _build_full_decision_table(self, condition_stubs: list[dict],
                                    action_stubs: list[dict]) -> dict:
        """穷举所有条件组合构建完整判定表（2^n 组合，仅对 boolean 类型）。"""
        # 只对 boolean 类型条件进行全组合
        bool_conditions = [c for c in condition_stubs if c["type"] == "boolean"]
        n = len(bool_conditions)

        if n == 0:
            return {"headers": [], "rules": []}

        headers = [c["name"] for c in condition_stubs] + [a["name"] for a in action_stubs]
        rules = []

        for combo_idx in range(2 ** n):
            # 构建 boolean 条件组合
            bool_values = {}
            for i in range(n):
                bool_values[bool_conditions[i]["name"]] = bool((combo_idx >> (n - 1 - i)) & 1)

            condition_values = []
            for cond in condition_stubs:
                if cond["type"] == "boolean":
                    condition_values.append(bool_values.get(cond["name"], False))
                else:
                    condition_values.append(cond["values"][0] if cond["values"] else None)

            # 自动推断动作值（简单规则: 所有 boolean 条件为 True 时成功）
            all_true = all(
                bool_values.get(c["name"], True)
                for c in bool_conditions
            )
            action_values = []
            for act in action_stubs:
                if act.get("result") == "success":
                    action_values.append(all_true)
                elif act.get("result") == "error":
                    action_values.append(not all_true)
                else:
                    action_values.append(False)

            rules.append({
                "condition_values": condition_values,
                "action_values": action_values,
                "simplified": False,
            })

        return {"headers": headers, "rules": rules}

    def generate_cases(self, analysis: dict, filtered_taxonomy: dict) -> list[TestCase]:
        """根据判定表分析结果生成测试用例，每个规则生成一个用例。

        Args:
            analysis: self.analyze() 的返回值
            filtered_taxonomy: FilteredTaxonomy 对象

        Returns:
            list[TestCase]
        """
        cases = []
        condition_stubs = analysis.get("condition_stubs", [])
        action_stubs = analysis.get("action_stubs", [])
        decision_table = analysis.get("decision_table", {})
        table_rules = decision_table.get("rules", [])
        actions = filtered_taxonomy.actions if hasattr(filtered_taxonomy, 'actions') else filtered_taxonomy.get("actions", [])
        assertions = filtered_taxonomy.assertions if hasattr(filtered_taxonomy, 'assertions') else filtered_taxonomy.get("assertions", [])

        if not table_rules:
            return cases

        # 确定常用操作
        fill_action = "fill" if "fill" in actions else (actions[0] if actions else "fill")
        click_action = "click" if "click" in actions else fill_action
        select_action = "select_option" if "select_option" in actions else fill_action
        check_action = "check" if "check" in actions else click_action

        default_assertion = "text_contains" if "text_contains" in assertions else (assertions[0] if assertions else "text_contains")

        for rule_idx, rule in enumerate(table_rules):
            case_id = f"DT-{rule_idx + 1:03d}"
            rule_num = rule_idx + 1

            condition_values = rule.get("condition_values", [])
            action_values = rule.get("action_values", [])

            # 构建操作步骤
            steps = []
            for i, cond in enumerate(condition_stubs):
                val = condition_values[i] if i < len(condition_values) else False
                field = cond.get("field", "")
                cond_type = cond.get("type", "boolean")

                if cond_type == "boolean":
                    if val:
                        if "required" in cond.get("name", "").lower() or "已填写" in cond.get("name", ""):
                            steps.append({
                                "action": fill_action,
                                "target": field,
                                "value": "正常值",
                                "desc": f"条件「{cond['name']}」= 是: 填写 {field}",
                            })
                        elif "范围" in cond.get("name", ""):
                            steps.append({
                                "action": fill_action,
                                "target": field,
                                "value": 50,
                                "desc": f"条件「{cond['name']}」= 是: 输入合法值",
                            })
                        else:
                            steps.append({
                                "action": check_action,
                                "target": field,
                                "value": True,
                                "desc": f"条件「{cond['name']}」= 是",
                            })
                    else:
                        if "required" in cond.get("name", "").lower() or "已填写" in cond.get("name", ""):
                            steps.append({
                                "action": "clear" if "clear" in actions else fill_action,
                                "target": field,
                                "value": "",
                                "desc": f"条件「{cond['name']}」= 否: 不填写 {field}",
                            })
                        elif "范围" in cond.get("name", ""):
                            steps.append({
                                "action": fill_action,
                                "target": field,
                                "value": -1,
                                "desc": f"条件「{cond['name']}」= 否: 输入非法值",
                            })
                        else:
                            steps.append({
                                "action": click_action,
                                "target": field,
                                "value": False,
                                "desc": f"条件「{cond['name']}」= 否: 不勾选 {field}",
                            })
                elif cond_type == "enum":
                    steps.append({
                        "action": select_action,
                        "target": field,
                        "value": val,
                        "desc": f"条件「{cond['name']}」= {val}: 选择 {field}",
                    })
                else:
                    steps.append({
                        "action": fill_action,
                        "target": field,
                        "value": val,
                        "desc": f"条件「{cond['name']}」= {val}",
                    })

            # 如果没有操作步骤，添加提交
            if not steps:
                steps.append({
                    "action": click_action,
                    "target": "submit_button",
                    "value": None,
                    "desc": "执行操作",
                })

            # 构建断言
            assertions_list = []
            has_triggered_action = False

            for i, act in enumerate(action_stubs):
                val = action_values[i] if i < len(action_values) else False
                if val:
                    has_triggered_action = True
                    result = act.get("result", "")
                    if result == "success":
                        assertions_list.append({
                            "type": default_assertion,
                            "target": "success_message",
                            "desc": f"动作「{act['name']}」触发: {act['desc']}",
                        })
                        if "element_exists" in assertions:
                            assertions_list.append({
                                "type": "element_exists",
                                "target": "success_message",
                                "desc": "成功提示出现",
                            })
                    elif result == "error":
                        assertions_list.append({
                            "type": default_assertion,
                            "target": "error_message",
                            "desc": f"动作「{act['name']}」触发: {act['desc']}",
                        })
                        if "element_exists" in assertions:
                            assertions_list.append({
                                "type": "element_exists",
                                "target": "error_message",
                                "desc": "错误提示出现",
                            })
                    else:
                        assertions_list.append({
                            "type": default_assertion,
                            "target": act.get("field", "form"),
                            "desc": f"动作「{act['name']}」触发: {act['desc']}",
                        })

            if not has_triggered_action:
                assertions_list.append({
                    "type": "no_error" if "no_error" in assertions else default_assertion,
                    "target": "form",
                    "desc": "预期无任何动作触发",
                })

            # 判定优先级
            has_error = any(
                action_values[i] and action_stubs[i].get("result") == "error"
                for i in range(min(len(action_values), len(action_stubs)))
            ) if action_values else False
            priority = "critical" if has_error else "high"

            # 构建名称
            cond_desc = ", ".join(
                f"{c['name']}={condition_values[i]}" if i < len(condition_values) else f"{c['name']}=?"
                for i, c in enumerate(condition_stubs)
            )
            act_desc = ", ".join(
                f"{a['name']}={action_values[i]}" if i < len(action_values) else f"{a['name']}=?"
                for i, a in enumerate(action_stubs)
            )

            cases.append(TestCase(
                case_id=case_id,
                name=f"判定表-规则{rule_num}: [{cond_desc}] => [{act_desc}]",
                method=self.method_name,
                category=self.method_category.value,
                priority=priority,
                module="决策规则",
                precondition="打开目标页面",
                test_data={
                    "rule_index": rule_idx,
                    "condition_values": {c["name"]: condition_values[i] for i, c in enumerate(condition_stubs) if i < len(condition_values)},
                    "action_values": {a["name"]: action_values[i] for i, a in enumerate(action_stubs) if i < len(action_values)},
                    "simplified": rule.get("simplified", False),
                },
                steps=steps,
                assertions=assertions_list,
                coverage_dimension=f"判定表: 规则{rule_num}",
                tags=["判定表", "决策表", f"规则{rule_num}"],
            ))

        return cases


# ── 独立运行测试 ──
if __name__ == "__main__":
    from .taxonomy_filter import FilteredTaxonomy

    requirement = {
        "title": "订单折扣计算",
        "description": "根据会员等级和订单金额计算折扣",
        "conditions": [
            {"name": "是VIP会员", "field": "member_level", "type": "boolean", "values": [True, False]},
            {"name": "订单金额≥100", "field": "order_amount", "type": "boolean", "values": [True, False]},
            {"name": "使用优惠券", "field": "coupon", "type": "boolean", "values": [True, False]},
        ],
        "actions": [
            {"name": "打8折", "desc": "享受8折优惠", "field": "discount", "result": "success"},
            {"name": "打9折", "desc": "享受9折优惠", "field": "discount", "result": "success"},
            {"name": "不打折", "desc": "原价购买", "field": "discount", "result": "error"},
        ],
        "rules": [
            {
                "condition_values": {"是VIP会员": True, "订单金额≥100": True, "使用优惠券": False},
                "action_values": {"打8折": True, "打9折": False, "不打折": False},
            },
            {
                "condition_values": {"是VIP会员": True, "订单金额≥100": False, "使用优惠券": False},
                "action_values": {"打8折": False, "打9折": True, "不打折": False},
            },
            {
                "condition_values": {"是VIP会员": False, "订单金额≥100": True, "使用优惠券": False},
                "action_values": {"打8折": False, "打9折": True, "不打折": False},
            },
            {
                "condition_values": {"是VIP会员": False, "订单金额≥100": False, "使用优惠券": False},
                "action_values": {"打8折": False, "打9折": False, "不打折": True},
            },
            {
                "condition_values": {"是VIP会员": True, "订单金额≥100": True, "使用优惠券": True},
                "action_values": {"打8折": False, "打9折": False, "不打折": True},
            },
        ],
    }

    ft = FilteredTaxonomy(
        actions=["fill", "click", "clear", "select_option", "check"],
        assertions=["text_contains", "element_exists", "element_checked", "select_value"],
    )

    dt = DecisionTable()
    print(f"方法: {dt.method_name}, 分类: {dt.method_category.value}")
    print(f"描述: {dt.description}")

    result = dt.analyze(requirement)
    print(f"分析摘要: {result['summary']}")
    print(f"条件桩: {[c['name'] for c in result['condition_stubs']]}")
    print(f"动作桩: {[a['name'] for a in result['action_stubs']]}")
    for i, rule in enumerate(result["decision_table"]["rules"]):
        print(f"  规则{i + 1}: 条件={rule['condition_values']} 动作={rule['action_values']}")

    cases = dt.generate_cases(result, ft)
    for c in cases:
        print(f"  {c.case_id} | {c.name} | {c.priority} | steps={len(c.steps)} assertions={len(c.assertions)}")
    print(f"总共生成 {len(cases)} 个用例")