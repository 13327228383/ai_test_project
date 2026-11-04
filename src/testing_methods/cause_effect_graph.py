# -*- coding: utf-8 -*-
"""因果图法 — 黑盒测试方法

通过因果图分析输入条件与输出结果之间的逻辑关系，生成判定表并设计测试用例。
"""

from typing import Optional
from .base import BaseMethod, TestCase, MethodCategory


class CauseEffectGraph(BaseMethod):
    method_name: str = "cause_effect_graph"
    method_category: MethodCategory = MethodCategory.BLACK_BOX
    description: str = "通过因果图分析输入条件与输出结果之间的逻辑关系，生成判定表并设计测试用例"
    applicable_scenarios: list[str] = ["条件判断", "逻辑组合", "业务规则", "权限控制", "状态联动"]

    def analyze(self, requirement: dict, context: Optional[dict] = None) -> dict:
        """分析需求中的条件（因）和结果（果），构建因果关系。

        Args:
            requirement: {
                title, description,
                rules: [
                    {name, conditions: [{field, operator, value}], effects: [{action, result, desc}]}
                ],
                fields: [{name, type, ...}],
            }
            context: 额外上下文

        Returns:
            {
                "causes": [{id, name, desc, field, operator, values}],
                "effects": [{id, name, desc, action, result}],
                "relations": [{cause_ids, effect_ids, operator, desc}],
                "decision_table": {
                    "headers": ["C1", "C2", ..., "E1", "E2", ...],
                    "rules": [[True/False, ..., True/False, ...], ...],
                },
                "summary": "..."
            }
        """
        rules = requirement.get("rules", [])
        fields = requirement.get("fields", [])

        causes = []
        effects = []
        relations = []

        # 从 rules 中提取因果
        cause_id_map = {}  # (field, operator, value) -> cause_id
        effect_id_map = {}  # (action, result) -> effect_id
        cause_index = 1
        effect_index = 1

        for rule in rules:
            rule_name = rule.get("name", "")
            conditions = rule.get("conditions", [])
            result_effects = rule.get("effects", [])

            rule_cause_ids = []
            rule_effect_ids = []

            # 提取/注册原因
            for cond in conditions:
                field = cond.get("field", "")
                operator = cond.get("operator", "equals")
                value = cond.get("value", None)
                desc = cond.get("desc", f"{field} {operator} {value}")

                key = (field, operator, str(value))
                if key not in cause_id_map:
                    cause_id = f"C{cause_index}"
                    cause_id_map[key] = cause_id
                    cause_index += 1
                    causes.append({
                        "id": cause_id,
                        "name": f"条件{len(causes) + 1}",
                        "desc": desc,
                        "field": field,
                        "operator": operator,
                        "values": [value],
                    })
                else:
                    cause_id = cause_id_map[key]
                rule_cause_ids.append(cause_id)

            # 提取/注册结果
            for eff in result_effects:
                action = eff.get("action", "display")
                result = eff.get("result", None)
                desc = eff.get("desc", "")

                key = (action, str(result))
                if key not in effect_id_map:
                    effect_id = f"E{effect_index}"
                    effect_id_map[key] = effect_id
                    effect_index += 1
                    effects.append({
                        "id": effect_id,
                        "name": f"结果{len(effects) + 1}",
                        "desc": desc,
                        "action": action,
                        "result": result,
                    })
                else:
                    effect_id = effect_id_map[key]
                rule_effect_ids.append(effect_id)

            # 记录关系
            if rule_cause_ids and rule_effect_ids:
                relations.append({
                    "cause_ids": rule_cause_ids,
                    "effect_ids": rule_effect_ids,
                    "operator": rule.get("operator", "AND"),
                    "desc": rule_name,
                })

        # 如果没有显式 rules，尝试从 fields 推断
        if not rules and fields:
            causes, effects, relations = self._infer_from_fields(fields, requirement)

        # 构建判定表
        decision_table = self._build_decision_table(causes, effects, relations)

        return {
            "causes": causes,
            "effects": effects,
            "relations": relations,
            "decision_table": decision_table,
            "total_causes": len(causes),
            "total_effects": len(effects),
            "total_rules": len(decision_table.get("rules", [])),
            "summary": f"共 {len(causes)} 个原因，{len(effects)} 个结果，{len(relations)} 组关系，{len(decision_table.get('rules', []))} 条判定规则",
        }

    def _infer_from_fields(self, fields: list[dict], requirement: dict) -> tuple:
        """从 fields 推断因果关系（当没有显式 rules 时）。"""
        causes = []
        effects = []
        relations = []
        cause_index = 1
        effect_index = 1

        for field in fields:
            field_name = field.get("name", "unknown")
            field_type = field.get("type", "text").lower()
            required = field.get("required", False)
            constraints = field.get("constraints", {})

            if required:
                cause_id = f"C{cause_index}"
                causes.append({
                    "id": cause_id,
                    "name": f"填写{field_name}",
                    "desc": f"是否填写 {field_name}",
                    "field": field_name,
                    "operator": "filled",
                    "values": [True, False],
                })
                cause_id_map = {cause_id: True}
                cause_index += 1

                # 未填写 → 错误提示
                effect_id = f"E{effect_index}"
                effects.append({
                    "id": effect_id,
                    "name": f"显示{field_name}错误",
                    "desc": f"显示 {field_name} 必填错误提示",
                    "action": "display",
                    "result": "error",
                })
                effect_index += 1

                relations.append({
                    "cause_ids": [cause_id],
                    "effect_ids": [effect_id],
                    "operator": "NOT",
                    "desc": f"{field_name} 为空时显示错误",
                })

            if constraints.get("min") is not None or constraints.get("max") is not None:
                cause_id = f"C{cause_index}"
                causes.append({
                    "id": cause_id,
                    "name": f"{field_name}在范围内",
                    "desc": f"{field_name} 是否在合法范围内",
                    "field": field_name,
                    "operator": "in_range",
                    "values": [True, False],
                })
                cause_index += 1

                effect_id = f"E{effect_index}"
                effects.append({
                    "id": effect_id,
                    "name": f"显示{field_name}范围错误",
                    "desc": f"显示 {field_name} 超出范围错误提示",
                    "action": "display",
                    "result": "error",
                })
                effect_index += 1

                relations.append({
                    "cause_ids": [cause_id],
                    "effect_ids": [effect_id],
                    "operator": "NOT",
                    "desc": f"{field_name} 超出范围时显示错误",
                })

        return causes, effects, relations

    def _build_decision_table(self, causes: list[dict], effects: list[dict], relations: list[dict]) -> dict:
        """根据因果图构建判定表。

        对每个原因进行真假组合，根据关系判断每个组合下各结果的真假值。
        """
        n = len(causes)
        if n == 0:
            return {"headers": [], "rules": []}

        # 表头
        headers = [c["id"] for c in causes] + [e["id"] for e in effects]

        # 生成所有原因的组合 (2^n)
        rules = []
        for combo_idx in range(2 ** n):
            # 生成原因值（True/False）
            cause_values = []
            for i in range(n):
                cause_values.append(bool((combo_idx >> (n - 1 - i)) & 1))

            # 根据关系计算效果值
            effect_values = self._evaluate_effects(cause_values, causes, effects, relations)

            # 构建规则行
            row = cause_values + effect_values
            rules.append(row)

        return {"headers": headers, "rules": rules}

    def _evaluate_effects(self, cause_values: list[bool], causes: list[dict],
                           effects: list[dict], relations: list[dict]) -> list[bool]:
        """根据当前原因组合和关系，计算每个结果的值。"""
        cause_map = {c["id"]: cause_values[i] for i, c in enumerate(causes)}
        effect_values = [False] * len(effects)
        effect_map = {e["id"]: i for i, e in enumerate(effects)}

        for rel in relations:
            rel_cause_vals = [cause_map.get(cid, False) for cid in rel["cause_ids"]]
            rel_op = rel.get("operator", "AND")

            if rel_op == "AND":
                cause_result = all(rel_cause_vals)
            elif rel_op == "OR":
                cause_result = any(rel_cause_vals)
            elif rel_op == "NOT":
                cause_result = not all(rel_cause_vals)
            elif rel_op == "NAND":
                cause_result = not all(rel_cause_vals)
            elif rel_op == "NOR":
                cause_result = not any(rel_cause_vals)
            else:
                cause_result = all(rel_cause_vals)

            for eid in rel["effect_ids"]:
                if eid in effect_map:
                    effect_values[effect_map[eid]] = cause_result

        return effect_values

    def generate_cases(self, analysis: dict, filtered_taxonomy: dict) -> list[TestCase]:
        """根据因果图分析结果和判定表生成测试用例。

        Args:
            analysis: self.analyze() 的返回值
            filtered_taxonomy: FilteredTaxonomy 对象

        Returns:
            list[TestCase]
        """
        cases = []
        causes = analysis.get("causes", [])
        effects = analysis.get("effects", [])
        decision_table = analysis.get("decision_table", {})
        rules = decision_table.get("rules", [])
        headers = decision_table.get("headers", [])
        actions = filtered_taxonomy.actions if hasattr(filtered_taxonomy, 'actions') else filtered_taxonomy.get("actions", [])
        assertions = filtered_taxonomy.assertions if hasattr(filtered_taxonomy, 'assertions') else filtered_taxonomy.get("assertions", [])

        if not rules:
            return cases

        # 确定常用操作
        fill_action = "fill" if "fill" in actions else (actions[0] if actions else "fill")
        click_action = "click" if "click" in actions else fill_action
        select_action = "select_option" if "select_option" in actions else fill_action
        check_action = "check" if "check" in actions else click_action
        uncheck_action = "uncheck" if "uncheck" in actions else click_action

        # 默认断言
        default_assertion = "text_contains" if "text_contains" in assertions else (assertions[0] if assertions else "text_contains")

        for rule_idx, rule in enumerate(rules):
            case_id = f"CEG-{rule_idx + 1:03d}"
            rule_num = rule_idx + 1

            # 分离原因和结果
            n_causes = len(causes)
            cause_values = rule[:n_causes]
            effect_values = rule[n_causes:]

            # 构建步骤
            steps = []
            for i, (cause, val) in enumerate(zip(causes, cause_values)):
                field = cause.get("field", "")
                operator = cause.get("operator", "equals")

                if operator == "filled":
                    if val:
                        steps.append({
                            "action": fill_action,
                            "target": field,
                            "value": "正常值",
                            "desc": f"填写 {field}（原因 {cause['id']}=True）",
                        })
                    else:
                        steps.append({
                            "action": "clear" if "clear" in actions else fill_action,
                            "target": field,
                            "value": "",
                            "desc": f"不填写 {field}（原因 {cause['id']}=False）",
                        })
                elif operator == "in_range":
                    if val:
                        steps.append({
                            "action": fill_action,
                            "target": field,
                            "value": 50,
                            "desc": f"输入合法值 {field}（原因 {cause['id']}=True）",
                        })
                    else:
                        steps.append({
                            "action": fill_action,
                            "target": field,
                            "value": -1,
                            "desc": f"输入非法值 {field}（原因 {cause['id']}=False）",
                        })
                elif operator == "equals":
                    values = cause.get("values", [])
                    if val and values:
                        steps.append({
                            "action": select_action if "select" in field.lower() else fill_action,
                            "target": field,
                            "value": values[0],
                            "desc": f"设置 {field}={values[0]}（原因 {cause['id']}=True）",
                        })
                    else:
                        steps.append({
                            "action": fill_action,
                            "target": field,
                            "value": "其他值",
                            "desc": f"设置 {field} 为其他值（原因 {cause['id']}=False）",
                        })
                elif operator in ("checked", "selected"):
                    steps.append({
                        "action": check_action if val else uncheck_action,
                        "target": field,
                        "value": val,
                        "desc": f"{'勾选' if val else '取消勾选'} {field}（原因 {cause['id']}={val}）",
                    })
                else:
                    steps.append({
                        "action": fill_action,
                        "target": field,
                        "value": "正常值" if val else "",
                        "desc": f"设置 {field}（原因 {cause['id']}={val}）",
                    })

            # 如果没有任何操作步骤，添加一个基本步骤
            if not steps:
                steps.append({
                    "action": click_action,
                    "target": "submit_button",
                    "value": None,
                    "desc": "提交表单",
                })

            # 构建断言
            assertions_list = []
            has_any_effect = False

            for i, (effect, val) in enumerate(zip(effects, effect_values)):
                if val:
                    has_any_effect = True
                    assertions_list.append({
                        "type": default_assertion,
                        "target": effect.get("action", "form"),
                        "desc": f"预期结果 {effect['id']}: {effect['desc']}",
                    })

                    # 根据效果类型添加特定断言
                    if effect.get("action") == "display" and effect.get("result") == "error":
                        if "text_contains" in assertions:
                            assertions_list.append({
                                "type": "text_contains",
                                "target": "error_message",
                                "desc": f"显示错误提示: {effect['desc']}",
                            })
                        if "element_exists" in assertions:
                            assertions_list.append({
                                "type": "element_exists",
                                "target": "error_message",
                                "desc": f"出现错误元素: {effect['desc']}",
                            })
                    elif effect.get("action") == "display" and effect.get("result") == "success":
                        if "text_contains" in assertions:
                            assertions_list.append({
                                "type": "text_contains",
                                "target": "success_message",
                                "desc": f"显示成功提示: {effect['desc']}",
                            })

            if not has_any_effect:
                # 没有预期效果 = 所有结果都不触发
                assertions_list.append({
                    "type": "no_error" if "no_error" in assertions else default_assertion,
                    "target": "form",
                    "desc": "预期无任何错误提示",
                })

            # 确定优先级
            has_error = any(
                effect_values[i] and effects[i].get("result") == "error"
                for i in range(len(effects))
            )
            priority = "critical" if has_error else "high"

            # 构建名称
            cause_desc = ", ".join(
                f"{c['id']}={v}" for c, v in zip(causes, cause_values)
            )
            effect_desc = ", ".join(
                f"{e['id']}={v}" for e, v in zip(effects, effect_values)
            )

            cases.append(TestCase(
                case_id=case_id,
                name=f"因果图-规则{rule_num}: [{cause_desc}] → [{effect_desc}]",
                method=self.method_name,
                category=self.method_category.value,
                priority=priority,
                module="业务规则",
                precondition="打开目标页面",
                test_data={
                    "rule_index": rule_idx,
                    "cause_values": {c["id"]: v for c, v in zip(causes, cause_values)},
                    "effect_values": {e["id"]: v for e, v in zip(effects, effect_values)},
                    "decision_table_row": rule_num,
                },
                steps=steps,
                assertions=assertions_list,
                coverage_dimension=f"因果图: 规则{rule_num}",
                tags=["因果图", "判定表", f"规则{rule_num}"],
            ))

        return cases


# ── 独立运行测试 ──
if __name__ == "__main__":
    from .taxonomy_filter import FilteredTaxonomy

    requirement = {
        "title": "用户登录",
        "description": "用户登录功能，需要验证用户名、密码和验证码",
        "fields": [
            {"name": "username", "type": "text", "required": True},
            {"name": "password", "type": "text", "required": True},
            {"name": "captcha", "type": "text", "required": True},
        ],
        "rules": [
            {
                "name": "全部正确→登录成功",
                "conditions": [
                    {"field": "username", "operator": "filled", "value": True, "desc": "用户名已填写"},
                    {"field": "password", "operator": "filled", "value": True, "desc": "密码已填写"},
                    {"field": "captcha", "operator": "filled", "value": True, "desc": "验证码已填写"},
                ],
                "effects": [
                    {"action": "display", "result": "success", "desc": "登录成功，跳转首页"},
                ],
                "operator": "AND",
            },
            {
                "name": "任一为空→显示错误",
                "conditions": [
                    {"field": "username", "operator": "filled", "value": True, "desc": "用户名已填写"},
                    {"field": "password", "operator": "filled", "value": True, "desc": "密码已填写"},
                    {"field": "captcha", "operator": "filled", "value": True, "desc": "验证码已填写"},
                ],
                "effects": [
                    {"action": "display", "result": "error", "desc": "显示必填项错误提示"},
                ],
                "operator": "NAND",
            },
        ],
    }

    ft = FilteredTaxonomy(
        actions=["fill", "click", "clear", "select_option", "check", "uncheck"],
        assertions=["text_contains", "text_not_contains", "element_exists", "element_enabled", "element_checked"],
    )

    ceg = CauseEffectGraph()
    print(f"方法: {ceg.method_name}, 分类: {ceg.method_category.value}")
    print(f"描述: {ceg.description}")

    result = ceg.analyze(requirement)
    print(f"分析摘要: {result['summary']}")
    print(f"原因: {[c['id'] + ':' + c['desc'] for c in result['causes']]}")
    print(f"结果: {[e['id'] + ':' + e['desc'] for e in result['effects']]}")
    print(f"判定表表头: {result['decision_table']['headers']}")
    for i, rule in enumerate(result["decision_table"]["rules"]):
        print(f"  规则{i + 1}: {rule}")

    cases = ceg.generate_cases(result, ft)
    for c in cases:
        print(f"  {c.case_id} | {c.name} | {c.priority} | steps={len(c.steps)} assertions={len(c.assertions)}")
    print(f"总共生成 {len(cases)} 个用例")