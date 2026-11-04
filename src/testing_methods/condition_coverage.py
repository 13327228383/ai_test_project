# -*- coding: utf-8 -*-
"""条件覆盖 (Condition Coverage) — 白盒测试方法

确保每个原子条件都取到真值和假值，覆盖复合条件中的所有子条件。
"""

import re
import itertools
import logging
from typing import Optional

from .base import BaseMethod, TestCase, MethodResult, MethodCategory

_log = logging.getLogger(__name__)


class ConditionCoverage(BaseMethod):
    """条件覆盖：将复合条件拆解为原子条件，每个原子条件取 True/False 各一次"""

    method_name: str = "condition_coverage"
    method_category: MethodCategory = MethodCategory.WHITE_BOX
    description: str = "确保每个原子条件都取到真值和假值，覆盖复合条件中的所有子条件"
    applicable_scenarios: list[str] = [
        "复合条件覆盖", "AND/OR/NOT 逻辑覆盖", "多条件组合",
        "权限组合条件", "业务规则组合", "原子条件验证",
    ]

    # ── 分析阶段 ──────────────────────────────────────────────────────────

    def analyze(self, requirement: dict, context: Optional[dict] = None) -> dict:
        """分析需求中的复合条件，拆解为原子条件。

        Args:
            requirement: {
                title, description,
                page_structure: {elements: [...]},
                code_branches: [{type, condition, ...}],
                business_rules: [{rule, conditions, ...}],
                ...
            }
            context: 额外上下文

        Returns:
            {
                compound_conditions: [{id, expression, atomic_conditions: [{id, name, operator}], ...}],
                atomic_conditions: [{id, name, parent_id, ...}],  # 展开后的原子条件列表
                total_compounds: int,
                total_atomics: int,
                source: "code" | "page_structure" | "simulated",
            }
        """
        code_branches = requirement.get("code_branches", [])
        business_rules = requirement.get("business_rules", [])
        compound_conditions = []
        source = "simulated"

        # 1. 从代码分支中提取复合条件
        if code_branches:
            source = "code"
            for idx, branch in enumerate(code_branches):
                condition_str = branch.get("condition", "")
                if condition_str:
                    comp = self._parse_compound_condition(
                        condition_str, f"CC-{idx + 1:03d}",
                        branch.get("description", ""), "code"
                    )
                    if comp:
                        compound_conditions.append(comp)

        # 2. 从业务规则中提取条件
        if business_rules:
            if source == "simulated":
                source = "business_rules"
            else:
                source = "mixed"
            start_idx = len(compound_conditions)
            for idx, rule in enumerate(business_rules):
                conditions = rule.get("conditions", [])
                rule_desc = rule.get("rule", rule.get("description", ""))
                if conditions:
                    # 构建复合条件表达式
                    logic = rule.get("logic", "AND")
                    expr_parts = []
                    atomic_conds = []
                    for ci, cond in enumerate(conditions):
                        atom_id = f"ATOM-{start_idx + idx + 1:03d}-{ci + 1}"
                        atom = {
                            "id": atom_id,
                            "name": cond.get("name", cond) if isinstance(cond, dict) else str(cond),
                            "operator": cond.get("operator", "==") if isinstance(cond, dict) else "==",
                            "expected_value": cond.get("expected_value", "True") if isinstance(cond, dict) else "True",
                            "description": cond.get("description", "") if isinstance(cond, dict) else str(cond),
                        }
                        atomic_conds.append(atom)
                        expr_parts.append(atom["name"])
                    expression = f" {logic} ".join(expr_parts)
                    compound_conditions.append({
                        "id": f"CC-{start_idx + idx + 1:03d}",
                        "expression": expression,
                        "logic": logic,
                        "atomic_conditions": atomic_conds,
                        "description": rule_desc or f"业务规则 {idx + 1}",
                        "source": "business_rules",
                    })

        # 3. 从需求描述中提取复合条件
        if not compound_conditions:
            source = "simulated"
            compound_conditions = self._simulate_compound_conditions(requirement)

        # 4. 展开所有原子条件
        all_atomics = []
        for comp in compound_conditions:
            for atom in comp.get("atomic_conditions", []):
                atom_copy = dict(atom)
                atom_copy["parent_id"] = comp["id"]
                all_atomics.append(atom_copy)

        _log.info("ConditionCoverage.analyze: %d compounds, %d atomics (source=%s)",
                   len(compound_conditions), len(all_atomics), source)

        return {
            "compound_conditions": compound_conditions,
            "atomic_conditions": all_atomics,
            "total_compounds": len(compound_conditions),
            "total_atomics": len(all_atomics),
            "source": source,
        }

    def _parse_compound_condition(self, condition_str: str, comp_id: str,
                                   description: str, source: str) -> Optional[dict]:
        """解析复合条件字符串，提取原子条件"""
        if not condition_str.strip():
            return None

        # 检测逻辑连接词
        logic = "AND"
        if re.search(r'\|\||\bOR\b|\bor\b|或者', condition_str, re.IGNORECASE):
            logic = "OR"
        elif re.search(r'\bNOT\b|\bnot\b|!|非', condition_str, re.IGNORECASE):
            logic = "NOT"

        # 拆分原子条件
        # 尝试按 AND/OR 符号拆分
        if logic == "AND":
            # 按 && 或 AND 拆分
            parts = re.split(r'&&|\bAND\b|\band\b|并且|且|同时', condition_str, flags=re.IGNORECASE)
        elif logic == "OR":
            parts = re.split(r'\|\||\bOR\b|\bor\b|或者|或', condition_str, flags=re.IGNORECASE)
        else:
            parts = [condition_str]

        atomic_conditions = []
        for i, part in enumerate(parts):
            part = part.strip().strip("()").strip()
            if not part:
                continue
            atom_id = f"{comp_id}-A{i + 1}"
            atomic_conditions.append({
                "id": atom_id,
                "name": part,
                "operator": self._detect_operator(part),
                "expected_value": "True",
                "description": part,
            })

        if not atomic_conditions:
            return None

        return {
            "id": comp_id,
            "expression": condition_str,
            "logic": logic,
            "atomic_conditions": atomic_conditions,
            "description": description or condition_str,
            "source": source,
        }

    def _detect_operator(self, condition: str) -> str:
        """检测条件中的操作符"""
        if ">=" in condition:
            return ">="
        elif "<=" in condition:
            return "<="
        elif "!=" in condition or "<>" in condition:
            return "!="
        elif "==" in condition or "=" in condition:
            return "=="
        elif ">" in condition:
            return ">"
        elif "<" in condition:
            return "<"
        elif "in" in condition.lower():
            return "in"
        elif "contains" in condition.lower():
            return "contains"
        return "=="

    def _simulate_compound_conditions(self, requirement: dict) -> list[dict]:
        """根据需求描述模拟复合条件"""
        description = requirement.get("description", "") + " " + requirement.get("title", "")
        compounds = []

        # 模拟常见复合条件
        simulated_patterns = [
            {
                "pattern": r"登录|认证|注册",
                "expression": "username_valid AND password_valid AND captcha_valid",
                "logic": "AND",
                "atoms": [
                    {"name": "username_valid", "operator": "==", "expected_value": "True",
                     "description": "用户名格式有效"},
                    {"name": "password_valid", "operator": "==", "expected_value": "True",
                     "description": "密码格式有效"},
                    {"name": "captcha_valid", "operator": "==", "expected_value": "True",
                     "description": "验证码正确"},
                ],
                "desc": "登录表单验证条件",
            },
            {
                "pattern": r"权限|管理员|角色",
                "expression": "is_authenticated AND (role == 'admin' OR role == 'manager')",
                "logic": "AND",
                "atoms": [
                    {"name": "is_authenticated", "operator": "==", "expected_value": "True",
                     "description": "用户已认证"},
                    {"name": "role == 'admin'", "operator": "==", "expected_value": "True",
                     "description": "角色为管理员"},
                    {"name": "role == 'manager'", "operator": "==", "expected_value": "True",
                     "description": "角色为经理"},
                ],
                "desc": "权限检查条件",
            },
            {
                "pattern": r"搜索|查询|筛选",
                "expression": "keyword_not_empty AND (category_selected OR date_range_set)",
                "logic": "AND",
                "atoms": [
                    {"name": "keyword_not_empty", "operator": "!=", "expected_value": "",
                     "description": "关键词不为空"},
                    {"name": "category_selected", "operator": "==", "expected_value": "True",
                     "description": "已选择分类"},
                    {"name": "date_range_set", "operator": "==", "expected_value": "True",
                     "description": "已设置日期范围"},
                ],
                "desc": "搜索条件组合",
            },
            {
                "pattern": r"表单|提交|验证",
                "expression": "required_fields_filled AND format_valid AND (agreement_checked OR skip_agreement)",
                "logic": "AND",
                "atoms": [
                    {"name": "required_fields_filled", "operator": "==", "expected_value": "True",
                     "description": "必填字段已填写"},
                    {"name": "format_valid", "operator": "==", "expected_value": "True",
                     "description": "格式校验通过"},
                    {"name": "agreement_checked", "operator": "==", "expected_value": "True",
                     "description": "已同意协议"},
                ],
                "desc": "表单提交条件",
            },
            {
                "pattern": r"支付|订单|购买",
                "expression": "stock_available AND balance_sufficient AND address_valid",
                "logic": "AND",
                "atoms": [
                    {"name": "stock_available", "operator": "==", "expected_value": "True",
                     "description": "库存充足"},
                    {"name": "balance_sufficient", "operator": "==", "expected_value": "True",
                     "description": "余额充足"},
                    {"name": "address_valid", "operator": "==", "expected_value": "True",
                     "description": "地址有效"},
                ],
                "desc": "下单条件",
            },
        ]

        for sp in simulated_patterns:
            if re.search(sp["pattern"], description):
                atomic_conds = []
                for i, atom_data in enumerate(sp["atoms"]):
                    atom_data["id"] = f"ATOM-SIM-{len(compounds) + 1:03d}-{i + 1}"
                    atomic_conds.append(atom_data)
                compounds.append({
                    "id": f"CC-SIM-{len(compounds) + 1:03d}",
                    "expression": sp["expression"],
                    "logic": sp["logic"],
                    "atomic_conditions": atomic_conds,
                    "description": sp["desc"],
                    "source": "simulated",
                })

        # 确保至少有一个复合条件
        if not compounds:
            compounds.append({
                "id": "CC-SIM-001",
                "expression": "condition_A AND condition_B",
                "logic": "AND",
                "atomic_conditions": [
                    {"id": "ATOM-SIM-001-1", "name": "condition_A", "operator": "==",
                     "expected_value": "True", "description": "条件A"},
                    {"id": "ATOM-SIM-001-2", "name": "condition_B", "operator": "==",
                     "expected_value": "True", "description": "条件B"},
                ],
                "description": "通用复合条件",
                "source": "simulated",
            })

        return compounds

    # ── 用例生成阶段 ──────────────────────────────────────────────────────

    def generate_cases(self, analysis: dict, filtered_taxonomy) -> list[TestCase]:
        """为每个原子条件生成 True/False 各一次的覆盖用例。

        Args:
            analysis: self.analyze() 的返回值
            filtered_taxonomy: FilteredTaxonomy 实例

        Returns:
            list[TestCase]
        """
        compound_conditions = analysis.get("compound_conditions", [])
        atomic_conditions = analysis.get("atomic_conditions", [])
        available_actions = filtered_taxonomy.actions if hasattr(filtered_taxonomy, 'actions') else filtered_taxonomy.get("actions", [])
        available_assertions = filtered_taxonomy.assertions if hasattr(filtered_taxonomy, 'assertions') else filtered_taxonomy.get("assertions", [])

        if not compound_conditions:
            _log.warning("ConditionCoverage: no compound conditions to cover")
            return []

        cases = []
        case_idx = 0

        # 策略1: 为每个原子条件生成 True/False 用例
        for atom in atomic_conditions:
            parent_id = atom.get("parent_id", "")
            atom_name = atom.get("name", "")
            atom_desc = atom.get("description", atom_name)

            # True 用例
            case_idx += 1
            true_case = self._build_atomic_case(
                case_id=f"CC-{case_idx:03d}",
                atom=atom,
                value_type="true",
                parent_id=parent_id,
                actions=available_actions,
                assertions=available_assertions,
            )
            cases.append(true_case)

            # False 用例
            case_idx += 1
            false_case = self._build_atomic_case(
                case_id=f"CC-{case_idx:03d}",
                atom=atom,
                value_type="false",
                parent_id=parent_id,
                actions=available_actions,
                assertions=available_assertions,
            )
            cases.append(false_case)

        # 策略2: 为每个复合条件生成组合覆盖用例（关键组合）
        if len(compound_conditions) <= 3:
            for comp in compound_conditions:
                combo_cases = self._generate_combination_cases(
                    comp, available_actions, available_assertions,
                    start_index=case_idx + 1,
                )
                cases.extend(combo_cases)
                case_idx += len(combo_cases)

        # 去重
        seen_ids = set()
        unique_cases = []
        for c in cases:
            if c.case_id not in seen_ids:
                seen_ids.add(c.case_id)
                unique_cases.append(c)

        _log.info("ConditionCoverage.generate_cases: %d cases from %d atomics",
                   len(unique_cases), len(atomic_conditions))
        return unique_cases

    def _build_atomic_case(self, case_id: str, atom: dict, value_type: str,
                            parent_id: str, actions: list[str],
                            assertions: list[str]) -> TestCase:
        """为单个原子条件构建 True/False 用例"""
        atom_name = atom.get("name", "")
        atom_desc = atom.get("description", atom_name)
        direction = "真值" if value_type == "true" else "假值"

        name = f"条件覆盖-{atom_desc}-{direction}"

        # 构建操作步骤
        steps = self._build_steps_for_atom(atom, value_type, actions)

        # 构建断言
        assertion_list = self._build_assertions_for_atom(atom, value_type, assertions)

        priority = "high" if value_type == "false" else "medium"

        return TestCase(
            case_id=case_id,
            name=name,
            method=self.method_name,
            category=self.method_category.value,
            priority=priority,
            module=f"atomic_condition",
            precondition=f"设置原子条件 '{atom_name}' 为 {value_type}",
            test_data={
                "atomic_condition": atom_name,
                "expected_value": value_type,
                "parent_compound": parent_id,
                "operator": atom.get("operator", "=="),
            },
            steps=steps,
            assertions=assertion_list,
            coverage_dimension=f"条件覆盖-{atom['id']}-{value_type}",
            tags=["condition_coverage", "atomic", value_type, "白盒"],
        )

    def _generate_combination_cases(self, compound: dict, actions: list[str],
                                     assertions: list[str],
                                     start_index: int) -> list[TestCase]:
        """为复合条件生成关键组合用例（MCDC 风格）"""
        atomic_conds = compound.get("atomic_conditions", [])
        if len(atomic_conds) < 2:
            return []

        logic = compound.get("logic", "AND")
        comp_desc = compound.get("description", "")
        cases = []

        # 生成关键组合：每个原子条件独立影响整体结果
        # 对于 AND：所有其他为 True，变化当前条件
        # 对于 OR：所有其他为 False，变化当前条件
        for i, focus_atom in enumerate(atomic_conds):
            case_id = f"CC-{start_index + i:03d}"

            # 构建其他条件的状态
            if logic == "AND":
                other_state = "true"  # 其他条件为 True，使当前条件起决定作用
            else:
                other_state = "false"  # 其他条件为 False，使当前条件起决定作用

            other_atoms = [a for a in atomic_conds if a["id"] != focus_atom["id"]]
            steps = []
            # 设置其他条件
            for oa in other_atoms:
                steps.extend(self._build_steps_for_atom(oa, other_state, actions))
            # 设置当前条件为 True
            steps.extend(self._build_steps_for_atom(focus_atom, "true", actions))

            name = f"条件覆盖-MCDC-{comp_desc}-{focus_atom.get('description', focus_atom.get('name', ''))}决定整体结果"

            assertion_list = [{
                "type": assertions[0] if assertions else "text_contains",
                "target": "overall-result",
                "desc": f"验证原子条件 '{focus_atom.get('name', '')}' 为真时整体结果为真",
            }]

            cases.append(TestCase(
                case_id=case_id,
                name=name,
                method=self.method_name,
                category=self.method_category.value,
                priority="high",
                module="compound_condition",
                precondition=f"设置其他原子条件为 {other_state}，聚焦 {focus_atom.get('name', '')}",
                test_data={
                    "compound": comp_desc,
                    "logic": logic,
                    "focus_atom": focus_atom.get("name", ""),
                    "other_state": other_state,
                },
                steps=steps,
                assertions=assertion_list,
                coverage_dimension=f"条件覆盖-MCDC-{compound['id']}",
                tags=["condition_coverage", "mcdc", logic, "白盒"],
            ))

        return cases

    def _build_steps_for_atom(self, atom: dict, value_type: str,
                               actions: list[str]) -> list[dict]:
        """根据原子条件构建操作步骤"""
        atom_name = atom.get("name", "").lower()
        atom_desc = atom.get("description", "")
        steps = []

        # 基于条件名称推断操作
        if "valid" in atom_name or "格式" in atom_desc:
            if value_type == "true":
                action = "fill" if "fill" in actions else "type_text" if "type_text" in actions else "click"
                steps.append({"action": action, "target": "input-field",
                              "value": "valid_value", "desc": f"填入有效值: {atom_desc}"})
            else:
                action = "fill" if "fill" in actions else "type_text" if "type_text" in actions else "click"
                steps.append({"action": action, "target": "input-field",
                              "value": "invalid@@@", "desc": f"填入无效值: {atom_desc}"})

        elif "filled" in atom_name or "必填" in atom_desc:
            if value_type == "true":
                action = "fill" if "fill" in actions else "type_text" if "type_text" in actions else "click"
                steps.append({"action": action, "target": "required-field",
                              "value": "test_value", "desc": f"填写必填字段: {atom_desc}"})
            else:
                action = "clear" if "clear" in actions else "click"
                steps.append({"action": action, "target": "required-field",
                              "value": "", "desc": f"清空必填字段: {atom_desc}"})

        elif "checked" in atom_name or "勾选" in atom_desc:
            if value_type == "true":
                action = "check" if "check" in actions else "click"
                steps.append({"action": action, "target": "checkbox-field",
                              "value": "true", "desc": f"勾选: {atom_desc}"})
            else:
                action = "uncheck" if "uncheck" in actions else "click"
                steps.append({"action": action, "target": "checkbox-field",
                              "value": "false", "desc": f"取消勾选: {atom_desc}"})

        elif "selected" in atom_name or "下拉" in atom_desc or "选择" in atom_desc:
            if value_type == "true":
                action = "select_option" if "select_option" in actions else "click"
                steps.append({"action": action, "target": "select-field",
                              "value": "valid_option", "desc": f"选择有效选项: {atom_desc}"})
            else:
                action = "select_option" if "select_option" in actions else "click"
                steps.append({"action": action, "target": "select-field",
                              "value": "", "desc": f"不选择/选择空: {atom_desc}"})

        elif "authenticated" in atom_name or "认证" in atom_desc or "登录" in atom_desc:
            if value_type == "true":
                steps.append({"action": "click" if "click" in actions else (actions[0] if actions else "click"),
                              "target": "login-button", "value": "",
                              "desc": "执行登录操作"})
            else:
                steps.append({"action": "click" if "click" in actions else (actions[0] if actions else "click"),
                              "target": "logout-button", "value": "",
                              "desc": "确保未登录状态"})

        elif "role" in atom_name or "管理员" in atom_desc or "角色" in atom_desc:
            if value_type == "true":
                steps.append({"action": "click" if "click" in actions else (actions[0] if actions else "click"),
                              "target": "admin-login", "value": "",
                              "desc": f"以管理员角色登录"})
            else:
                steps.append({"action": "click" if "click" in actions else (actions[0] if actions else "click"),
                              "target": "user-login", "value": "",
                              "desc": f"以普通用户角色登录"})

        elif "available" in atom_name or "stock" in atom_name or "库存" in atom_desc:
            if value_type == "true":
                steps.append({"action": "fill" if "fill" in actions else "click",
                              "target": "quantity-input", "value": "1",
                              "desc": "输入库存范围内的数量"})
            else:
                steps.append({"action": "fill" if "fill" in actions else "click",
                              "target": "quantity-input", "value": "99999",
                              "desc": "输入超出库存的数量"})

        elif "sufficient" in atom_name or "余额" in atom_desc or "balance" in atom_name:
            if value_type == "true":
                steps.append({"action": "fill" if "fill" in actions else "click",
                              "target": "amount-input", "value": "100",
                              "desc": "输入余额范围内的金额"})
            else:
                steps.append({"action": "fill" if "fill" in actions else "click",
                              "target": "amount-input", "value": "999999",
                              "desc": "输入超出余额的金额"})

        else:
            # 通用操作
            primary_action = actions[0] if actions else "click"
            if primary_action in ("fill", "type_text"):
                value = "valid" if value_type == "true" else "invalid"
                steps.append({"action": primary_action, "target": "condition-input",
                              "value": value, "desc": f"设置原子条件 '{atom_desc}' 为{value_type}"})
            elif primary_action == "check":
                steps.append({"action": primary_action, "target": "condition-checkbox",
                              "value": value_type, "desc": f"设置原子条件 '{atom_desc}' 为{value_type}"})
            else:
                steps.append({"action": primary_action, "target": "condition-trigger",
                              "value": "", "desc": f"触发条件 '{atom_desc}' 为{value_type}"})

        return steps

    def _build_assertions_for_atom(self, atom: dict, value_type: str,
                                    available_assertions: list[str]) -> list[dict]:
        """为原子条件构建断言"""
        atom_desc = atom.get("description", atom.get("name", ""))
        assertions = []

        if value_type == "true":
            # 条件为真时，期望正向结果
            if "text_contains" in available_assertions:
                assertions.append({
                    "type": "text_contains",
                    "target": "success-indicator",
                    "desc": f"验证原子条件 '{atom_desc}' 为真时，正向结果出现",
                })
            elif "element_exists" in available_assertions:
                assertions.append({
                    "type": "element_exists",
                    "target": "success-element",
                    "desc": f"验证原子条件 '{atom_desc}' 为真时，成功元素存在",
                })
            if "element_enabled" in available_assertions:
                assertions.append({
                    "type": "element_enabled",
                    "target": "submit-button",
                    "desc": "验证提交按钮可用",
                })
        else:
            # 条件为假时，期望负向结果
            if "text_not_contains" in available_assertions:
                assertions.append({
                    "type": "text_not_contains",
                    "target": "error-message",
                    "desc": f"验证原子条件 '{atom_desc}' 为假时，应有错误提示",
                })
            elif "text_contains" in available_assertions:
                assertions.append({
                    "type": "text_contains",
                    "target": "error-message",
                    "desc": f"验证原子条件 '{atom_desc}' 为假时，显示错误提示",
                })
            if "element_enabled" in available_assertions:
                assertions.append({
                    "type": "element_enabled",
                    "target": "submit-button",
                    "desc": "验证提交按钮可能被禁用",
                })

        # 确保至少有一个断言
        if not assertions and available_assertions:
            assertions.append({
                "type": available_assertions[0],
                "target": "result-indicator",
                "desc": f"验证原子条件 '{atom_desc}' 为{value_type}时的结果",
            })

        return assertions


# ── 独立运行测试 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    from .taxonomy_filter import FilteredTaxonomy

    # 模拟需求数据
    requirement = {
        "title": "用户注册页面",
        "description": "用户注册需要填写用户名、密码、确认密码、邮箱，并勾选同意协议。用户名和密码需要格式验证。",
        "page_structure": {
            "elements": [
                {"tag": "input", "type": "text", "id": "username", "text": "用户名"},
                {"tag": "input", "type": "password", "id": "password", "text": "密码"},
                {"tag": "input", "type": "password", "id": "confirm", "text": "确认密码"},
                {"tag": "input", "type": "email", "id": "email", "text": "邮箱"},
                {"tag": "input", "type": "checkbox", "id": "agreement", "text": "同意协议"},
                {"tag": "button", "id": "btn-register", "text": "注册"},
            ],
        },
        "business_rules": [
            {
                "rule": "注册表单验证",
                "logic": "AND",
                "conditions": [
                    {"name": "username_valid", "operator": "==", "expected_value": "True", "description": "用户名格式有效"},
                    {"name": "password_valid", "operator": "==", "expected_value": "True", "description": "密码格式有效"},
                    {"name": "password_match", "operator": "==", "expected_value": "True", "description": "两次密码一致"},
                    {"name": "email_valid", "operator": "==", "expected_value": "True", "description": "邮箱格式有效"},
                    {"name": "agreement_checked", "operator": "==", "expected_value": "True", "description": "已同意协议"},
                ],
            },
        ],
    }

    ft = FilteredTaxonomy(
        actions=["fill", "click", "check", "uncheck", "clear", "select_option", "type_text"],
        assertions=["text_contains", "text_not_contains", "element_enabled",
                     "element_checked", "element_exists", "no_error"],
    )

    method = ConditionCoverage()
    print(f"=== {method.method_name} ===")
    print(f"Category: {method.method_category.value}")
    print(f"Description: {method.description}")

    analysis = method.analyze(requirement)
    print(f"\nAnalysis: {analysis['total_compounds']} compounds, {analysis['total_atomics']} atomics, source={analysis['source']}")
    for atom in analysis["atomic_conditions"]:
        print(f"  [{atom['id']}] {atom['name']} ({atom['description']})")

    cases = method.generate_cases(analysis, ft)
    print(f"\nGenerated {len(cases)} test cases:")
    for c in cases:
        print(f"  [{c.case_id}] {c.name} | steps={len(c.steps)} | assertions={len(c.assertions)}")

    result = method.run(requirement, ft)
    print(f"\nResult: {result.total_cases} cases, coverage={result.coverage_report}")