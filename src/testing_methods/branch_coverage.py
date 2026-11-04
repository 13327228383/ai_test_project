# -*- coding: utf-8 -*-
"""分支覆盖/判定覆盖 (Branch/Decision Coverage) — 白盒测试方法

确保每个判定条件的真/假分支都被执行，覆盖所有可能的执行路径分支。
"""

import re
import logging
from typing import Optional

from .base import BaseMethod, TestCase, MethodResult, MethodCategory

_log = logging.getLogger(__name__)


class BranchCoverage(BaseMethod):
    """分支覆盖：确保每个判定条件的 True/False 路径都被覆盖"""

    method_name: str = "branch_coverage"
    method_category: MethodCategory = MethodCategory.WHITE_BOX
    description: str = "确保每个判定条件的真/假分支都被执行，覆盖所有可能的执行路径分支"
    applicable_scenarios: list[str] = [
        "条件分支覆盖", "权限分支测试", "状态分支测试",
        "if/else 覆盖", "switch/case 覆盖", "决策路径覆盖",
    ]

    # ── 分析阶段 ──────────────────────────────────────────────────────────

    def analyze(self, requirement: dict, context: Optional[dict] = None) -> dict:
        """分析需求中的条件分支（if/else、权限、状态等），提取所有判定点。

        Args:
            requirement: {
                title, description,
                page_structure: {elements: [...]},
                code_branches: [{type, condition, true_branch, false_branch, location, ...}],
                permission_rules: [{role, allowed_actions, ...}],
                state_rules: [{state, transitions, ...}],
                ...
            }
            context: 额外上下文

        Returns:
            {
                branches: [{id, type, condition, true_path, false_path, description}],
                branch_types: {if_else: 2, permission: 3, state: 1, ...},
                total_branches: int,
                source: "code" | "page_structure" | "simulated",
            }
        """
        code_branches = requirement.get("code_branches", [])
        page_structure = requirement.get("page_structure", {})
        permission_rules = requirement.get("permission_rules", [])
        state_rules = requirement.get("state_rules", [])
        branches = []
        source = "simulated"

        # 1. 从代码分支中提取
        if code_branches:
            source = "code"
            for idx, branch in enumerate(code_branches):
                br_id = f"BR-{idx + 1:03d}"
                branches.append({
                    "id": br_id,
                    "type": branch.get("type", "if_else"),
                    "condition": branch.get("condition", ""),
                    "true_path": branch.get("true_branch", branch.get("true_path", "True 分支")),
                    "false_path": branch.get("false_branch", branch.get("false_path", "False 分支")),
                    "description": branch.get("description", f"Branch at {branch.get('location', 'unknown')}"),
                    "location": branch.get("location", ""),
                    "source": "code",
                })

        # 2. 从页面结构 + 权限规则中推导分支
        if page_structure or permission_rules or state_rules:
            if source == "simulated":
                source = "page_structure"
            else:
                source = "mixed"

            derived = self._derive_branches_from_structure(requirement)
            start_idx = len(branches)
            for idx, br in enumerate(derived):
                br["id"] = f"BR-{start_idx + idx + 1:03d}"
                branches.append(br)

        # 3. 从需求描述中模拟分支
        if not branches:
            source = "simulated"
            branches = self._simulate_branches_from_description(requirement)

        # 统计分支类型
        branch_types = {}
        for b in branches:
            bt = b.get("type", "unknown")
            branch_types[bt] = branch_types.get(bt, 0) + 1

        _log.info("BranchCoverage.analyze: %d branches found (source=%s)", len(branches), source)

        return {
            "branches": branches,
            "branch_types": branch_types,
            "total_branches": len(branches),
            "source": source,
        }

    def _derive_branches_from_structure(self, requirement: dict) -> list[dict]:
        """从页面结构和业务规则中推导条件分支"""
        branches = []
        description = requirement.get("description", "") + " " + requirement.get("title", "")

        # 权限分支
        permission_rules = requirement.get("permission_rules", [])
        if permission_rules:
            for role_rule in permission_rules:
                role = role_rule.get("role", "unknown")
                branches.append({
                    "type": "permission",
                    "condition": f"user.role == '{role}'",
                    "true_path": f"允许 {role_rule.get('allowed_actions', '操作')}",
                    "false_path": f"拒绝 {role} 权限操作",
                    "description": f"权限分支-{role}",
                    "source": "permission_rules",
                })
        else:
            # 从描述中检测权限关键词
            if re.search(r"权限|角色|管理员|普通用户|游客|登录|未登录", description):
                branches.append({
                    "type": "permission",
                    "condition": "user.is_authenticated",
                    "true_path": "显示已登录用户功能",
                    "false_path": "显示登录页面/游客功能",
                    "description": "登录状态分支",
                    "source": "simulated",
                })
                branches.append({
                    "type": "permission",
                    "condition": "user.role == 'admin'",
                    "true_path": "显示管理员功能",
                    "false_path": "显示普通用户功能",
                    "description": "管理员权限分支",
                    "source": "simulated",
                })

        # 状态分支
        state_rules = requirement.get("state_rules", [])
        if state_rules:
            for state_rule in state_rules:
                state = state_rule.get("state", "unknown")
                transitions = state_rule.get("transitions", [])
                for trans in transitions:
                    branches.append({
                        "type": "state",
                        "condition": f"current_state == '{state}'",
                        "true_path": f"允许 {trans.get('action', '操作')} -> {trans.get('target', '下一状态')}",
                        "false_path": f"禁止从 {state} 执行 {trans.get('action', '操作')}",
                        "description": f"状态分支-{state}->{trans.get('target', '?')}",
                        "source": "state_rules",
                    })

        # 页面元素中的条件分支（如禁用/启用状态）
        page_structure = requirement.get("page_structure", {})
        elements = page_structure.get("elements", []) if page_structure else []
        for el in elements:
            if el.get("disabled") is not None:
                tag = el.get("tag", "")
                el_id = el.get("id", "") or el.get("text", "")
                branches.append({
                    "type": "element_state",
                    "condition": f"element.{el_id}.enabled",
                    "true_path": f"元素 {el_id} 可交互",
                    "false_path": f"元素 {el_id} 禁用/不可交互",
                    "description": f"元素状态分支-{el_id}",
                    "source": "page_structure",
                })

        # 从需求关键词检测常见分支模式
        branch_patterns = [
            (r"if|如果|当|条件|判断|满足|否则|不满足",
             "conditional", "条件判断分支"),
            (r"验证|校验|检查|通过|未通过|失败|成功",
             "validation", "验证结果分支"),
            (r"显示|隐藏|可见|不可见|展开|收起|折叠",
             "visibility", "可见性分支"),
            (r"启用|禁用|可用|不可用|灰度|开|关",
             "toggle", "开关状态分支"),
            (r"大于|小于|等于|超过|低于|超出|范围内",
             "comparison", "比较判断分支"),
        ]

        for pattern, br_type, br_desc in branch_patterns:
            if re.search(pattern, description) and not any(
                b.get("type") == br_type for b in branches
            ):
                branches.append({
                    "type": br_type,
                    "condition": f"条件: {br_desc}",
                    "true_path": f"{br_desc}为真时的执行路径",
                    "false_path": f"{br_desc}为假时的执行路径",
                    "description": br_desc,
                    "source": "simulated",
                })

        return branches

    def _simulate_branches_from_description(self, requirement: dict) -> list[dict]:
        """根据需求描述模拟条件分支"""
        description = requirement.get("description", "") + " " + requirement.get("title", "")
        branches = []

        # 模拟常见分支
        if re.search(r"登录|注册|认证|登录状态", description):
            branches.append({
                "id": "BR-001", "type": "permission",
                "condition": "user.is_authenticated",
                "true_path": "用户已登录，进入主页",
                "false_path": "用户未登录，跳转登录页",
                "description": "登录状态判定",
                "source": "simulated",
            })

        if re.search(r"表单|提交|输入|验证", description):
            branches.append({
                "id": "BR-002", "type": "validation",
                "condition": "form.is_valid",
                "true_path": "表单验证通过，提交数据",
                "false_path": "表单验证失败，显示错误提示",
                "description": "表单验证分支",
                "source": "simulated",
            })

        if re.search(r"权限|管理员|角色|普通用户", description):
            branches.append({
                "id": "BR-003", "type": "permission",
                "condition": "user.role == 'admin'",
                "true_path": "管理员权限，显示全部功能",
                "false_path": "非管理员，显示受限功能",
                "description": "管理员权限分支",
                "source": "simulated",
            })

        if re.search(r"搜索|查询|筛选|条件", description):
            branches.append({
                "id": "BR-004", "type": "conditional",
                "condition": "search.has_results",
                "true_path": "有搜索结果，显示列表",
                "false_path": "无搜索结果，显示空状态",
                "description": "搜索结果分支",
                "source": "simulated",
            })

        if re.search(r"审批|审核|状态|通过|驳回", description):
            branches.append({
                "id": "BR-005", "type": "state",
                "condition": "approval.status == 'approved'",
                "true_path": "审批通过，进入下一流程",
                "false_path": "审批驳回，返回修改",
                "description": "审批状态分支",
                "source": "simulated",
            })

        # 确保至少有两个分支
        if len(branches) < 2:
            branches.append({
                "id": "BR-001", "type": "conditional",
                "condition": "条件A == True",
                "true_path": "条件A为真时执行路径A",
                "false_path": "条件A为假时执行路径B",
                "description": "通用条件分支",
                "source": "simulated",
            })
            branches.append({
                "id": "BR-002", "type": "conditional",
                "condition": "条件B == True",
                "true_path": "条件B为真时执行路径C",
                "false_path": "条件B为假时执行路径D",
                "description": "通用条件分支B",
                "source": "simulated",
            })

        return branches

    # ── 用例生成阶段 ──────────────────────────────────────────────────────

    def generate_cases(self, analysis: dict, filtered_taxonomy) -> list[TestCase]:
        """为每个分支生成 True/False 两条路径的覆盖用例。

        Args:
            analysis: self.analyze() 的返回值
            filtered_taxonomy: FilteredTaxonomy 实例

        Returns:
            list[TestCase]
        """
        branches = analysis.get("branches", [])
        available_actions = filtered_taxonomy.actions if hasattr(filtered_taxonomy, 'actions') else filtered_taxonomy.get("actions", [])
        available_assertions = filtered_taxonomy.assertions if hasattr(filtered_taxonomy, 'assertions') else filtered_taxonomy.get("assertions", [])

        if not branches:
            _log.warning("BranchCoverage: no branches to cover")
            return []

        cases = []
        case_idx = 0

        for branch in branches:
            br_id = branch.get("id", f"BR-{case_idx + 1:03d}")
            br_type = branch.get("type", "unknown")
            condition = branch.get("condition", "")
            true_path = branch.get("true_path", "True 分支")
            false_path = branch.get("false_path", "False 分支")
            description = branch.get("description", "")

            # True 分支用例
            case_idx += 1
            true_case = self._build_branch_case(
                case_id=f"BC-{case_idx:03d}",
                branch=branch,
                path_type="true",
                path_desc=true_path,
                condition=condition,
                actions=available_actions,
                assertions=available_assertions,
            )
            cases.append(true_case)

            # False 分支用例
            case_idx += 1
            false_case = self._build_branch_case(
                case_id=f"BC-{case_idx:03d}",
                branch=branch,
                path_type="false",
                path_desc=false_path,
                condition=condition,
                actions=available_actions,
                assertions=available_assertions,
            )
            cases.append(false_case)

        _log.info("BranchCoverage.generate_cases: %d cases from %d branches",
                   len(cases), len(branches))
        return cases

    def _build_branch_case(self, case_id: str, branch: dict, path_type: str,
                            path_desc: str, condition: str, actions: list[str],
                            assertions: list[str]) -> TestCase:
        """为单个分支路径构建测试用例"""
        br_type = branch.get("type", "unknown")
        description = branch.get("description", "")
        direction = "True路径" if path_type == "true" else "False路径"

        name = f"分支覆盖-{description}-{direction}"

        # 根据分支类型选择操作
        steps = self._build_steps_for_branch(br_type, path_type, actions)

        # 根据分支类型选择断言
        assertion_list = self._build_assertions_for_branch(br_type, path_type, path_desc, assertions)

        # 判断优先级
        priority = "high" if br_type in ("permission", "validation") else "medium"

        return TestCase(
            case_id=case_id,
            name=name,
            method=self.method_name,
            category=self.method_category.value,
            priority=priority,
            module=br_type,
            precondition=f"满足条件: {condition} == {path_type.capitalize()}",
            test_data={
                "branch_type": br_type,
                "condition": condition,
                "path_type": path_type,
                "expected_path": path_desc,
            },
            steps=steps,
            assertions=assertion_list,
            coverage_dimension=f"分支覆盖-{br_type}-{path_type}",
            tags=["branch_coverage", br_type, path_type, "白盒"],
        )

    def _build_steps_for_branch(self, br_type: str, path_type: str,
                                 actions: list[str]) -> list[dict]:
        """根据分支类型构建操作步骤"""
        steps = []

        if br_type == "permission":
            if path_type == "true":
                if "click" in actions:
                    steps.append({"action": "click", "target": "login-button",
                                  "value": "", "desc": "以有权限用户登录"})
                if "navigate" in actions:
                    steps.append({"action": "navigate", "target": "/",
                                  "value": "", "desc": "导航到目标页面"})
                if "click" in actions:
                    steps.append({"action": "click", "target": "protected-action",
                                  "value": "", "desc": "执行受权限保护的操作"})
            else:
                if "navigate" in actions:
                    steps.append({"action": "navigate", "target": "/",
                                  "value": "", "desc": "以无权限用户访问"})
                if "click" in actions:
                    steps.append({"action": "click", "target": "protected-action",
                                  "value": "", "desc": "尝试执行受权限保护的操作"})

        elif br_type == "validation":
            if path_type == "true":
                if "fill" in actions:
                    steps.append({"action": "fill", "target": "form-input",
                                  "value": "valid_value", "desc": "填入有效数据"})
                if "click" in actions:
                    steps.append({"action": "click", "target": "submit-button",
                                  "value": "", "desc": "点击提交按钮"})
            else:
                if "fill" in actions:
                    steps.append({"action": "fill", "target": "form-input",
                                  "value": "invalid_value", "desc": "填入无效数据"})
                elif "clear" in actions:
                    steps.append({"action": "clear", "target": "form-input",
                                  "value": "", "desc": "清空输入框"})
                if "click" in actions:
                    steps.append({"action": "click", "target": "submit-button",
                                  "value": "", "desc": "点击提交按钮"})

        elif br_type == "state":
            if path_type == "true":
                if "click" in actions:
                    steps.append({"action": "click", "target": "state-transition-trigger",
                                  "value": "", "desc": "触发状态变更操作"})
                if "wait" in actions:
                    steps.append({"action": "wait", "target": "",
                                  "value": "1000", "desc": "等待状态变更完成"})
            else:
                if "click" in actions:
                    steps.append({"action": "click", "target": "blocked-action",
                                  "value": "", "desc": "尝试执行非法的状态转换"})

        elif br_type == "visibility":
            if path_type == "true":
                if "click" in actions:
                    steps.append({"action": "click", "target": "toggle-button",
                                  "value": "", "desc": "点击展开/显示按钮"})
            else:
                if "click" in actions:
                    steps.append({"action": "click", "target": "toggle-button",
                                  "value": "", "desc": "点击收起/隐藏按钮"})

        elif br_type == "element_state":
            if path_type == "true":
                if "click" in actions:
                    steps.append({"action": "click", "target": "enabled-element",
                                  "value": "", "desc": "操作启用状态的元素"})
                elif "fill" in actions:
                    steps.append({"action": "fill", "target": "enabled-element",
                                  "value": "test", "desc": "在启用状态的输入框中输入"})
            else:
                if "click" in actions:
                    steps.append({"action": "click", "target": "disabled-element",
                                  "value": "", "desc": "尝试点击禁用状态的元素（应无响应）"})

        else:
            # 通用操作
            if path_type == "true":
                if "click" in actions:
                    steps.append({"action": "click", "target": "main-action",
                                  "value": "", "desc": f"执行条件满足时的操作"})
                elif "fill" in actions:
                    steps.append({"action": "fill", "target": "main-input",
                                  "value": "valid", "desc": f"填入满足条件的值"})
            else:
                if "click" in actions:
                    steps.append({"action": "click", "target": "alt-action",
                                  "value": "", "desc": f"执行条件不满足时的操作"})
                elif "fill" in actions:
                    steps.append({"action": "fill", "target": "main-input",
                                  "value": "invalid", "desc": f"填入不满足条件的值"})

        # 确保至少有一个步骤
        if not steps:
            primary_action = "click" if "click" in actions else (actions[0] if actions else "click")
            steps.append({"action": primary_action, "target": "target-element",
                          "value": "", "desc": f"执行分支{path_type}路径操作"})

        return steps

    def _build_assertions_for_branch(self, br_type: str, path_type: str,
                                      path_desc: str,
                                      available_assertions: list[str]) -> list[dict]:
        """根据分支类型构建断言"""
        assertions = []

        if br_type == "permission":
            if path_type == "true":
                assertions.append({
                    "type": "element_exists" if "element_exists" in available_assertions else (available_assertions[0] if available_assertions else "text_contains"),
                    "target": "protected-content",
                    "desc": "验证受保护内容可见",
                })
            else:
                assertions.append({
                    "type": "element_not_exists" if "element_not_exists" in available_assertions else "text_not_contains" if "text_not_contains" in available_assertions else (available_assertions[0] if available_assertions else "text_contains"),
                    "target": "protected-content",
                    "desc": "验证受保护内容不可见或提示无权限",
                })

        elif br_type == "validation":
            if path_type == "true":
                assertions.append({
                    "type": "text_contains" if "text_contains" in available_assertions else (available_assertions[0] if available_assertions else "text_contains"),
                    "target": "success-message",
                    "desc": "验证提交成功提示",
                })
            else:
                assertions.append({
                    "type": "text_contains" if "text_contains" in available_assertions else (available_assertions[0] if available_assertions else "text_contains"),
                    "target": "error-message",
                    "desc": "验证错误提示信息",
                })

        elif br_type == "state":
            if path_type == "true":
                assertions.append({
                    "type": "text_contains" if "text_contains" in available_assertions else (available_assertions[0] if available_assertions else "text_contains"),
                    "target": "new-state-indicator",
                    "desc": "验证状态变更成功",
                })
            else:
                assertions.append({
                    "type": "text_contains" if "text_contains" in available_assertions else (available_assertions[0] if available_assertions else "text_contains"),
                    "target": "error-prompt",
                    "desc": "验证不允许非法状态转换",
                })

        elif br_type == "visibility":
            if path_type == "true":
                assertions.append({
                    "type": "element_visible" if "element_visible" in available_assertions else (available_assertions[0] if available_assertions else "text_contains"),
                    "target": "toggle-content",
                    "desc": "验证内容已显示/展开",
                })
            else:
                assertions.append({
                    "type": "element_not_exists" if "element_not_exists" in available_assertions else "text_not_contains" if "text_not_contains" in available_assertions else (available_assertions[0] if available_assertions else "text_contains"),
                    "target": "toggle-content",
                    "desc": "验证内容已隐藏/收起",
                })

        else:
            # 通用断言
            assertion_type = available_assertions[0] if available_assertions else "text_contains"
            assertions.append({
                "type": assertion_type,
                "target": "result-indicator",
                "desc": f"验证分支{path_type}路径结果: {path_desc}",
            })

        # 添加一个通用断言确保完整性
        if "no_error" in available_assertions and len(assertions) < 2:
            assertions.append({
                "type": "no_error",
                "target": "",
                "desc": "验证无控制台错误",
            })

        return assertions


# ── 独立运行测试 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    from .taxonomy_filter import FilteredTaxonomy

    # 模拟需求数据
    requirement = {
        "title": "用户订单管理页面",
        "description": "管理员可以查看所有订单，普通用户只能查看自己的订单。订单包含待支付、已支付、已发货等状态。",
        "page_structure": {
            "elements": [
                {"tag": "button", "id": "btn-approve", "text": "审批通过"},
                {"tag": "button", "id": "btn-reject", "text": "驳回"},
                {"tag": "input", "id": "search-input", "type": "text"},
            ],
        },
        "permission_rules": [
            {"role": "admin", "allowed_actions": "查看所有订单、审批订单"},
            {"role": "user", "allowed_actions": "查看自己的订单"},
        ],
        "state_rules": [
            {"state": "待支付", "transitions": [
                {"action": "支付", "target": "已支付"},
                {"action": "取消", "target": "已取消"},
            ]},
            {"state": "已支付", "transitions": [
                {"action": "发货", "target": "已发货"},
            ]},
        ],
    }

    ft = FilteredTaxonomy(
        actions=["navigate", "click", "fill", "select_option", "check", "wait"],
        assertions=["text_contains", "text_not_contains", "element_exists",
                     "element_not_exists", "element_visible", "url_matches", "no_error"],
    )

    method = BranchCoverage()
    print(f"=== {method.method_name} ===")
    print(f"Category: {method.method_category.value}")
    print(f"Description: {method.description}")

    analysis = method.analyze(requirement)
    print(f"\nAnalysis: {analysis['total_branches']} branches, source={analysis['source']}")
    print(f"Branch types: {analysis['branch_types']}")
    for b in analysis["branches"]:
        print(f"  [{b['id']}] {b['type']}: {b['condition']} -> T:{b['true_path'][:40]} | F:{b['false_path'][:40]}")

    cases = method.generate_cases(analysis, ft)
    print(f"\nGenerated {len(cases)} test cases:")
    for c in cases:
        print(f"  [{c.case_id}] {c.name} | steps={len(c.steps)} | assertions={len(c.assertions)}")

    result = method.run(requirement, ft)
    print(f"\nResult: {result.total_cases} cases, coverage={result.coverage_report}")