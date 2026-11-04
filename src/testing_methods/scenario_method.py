# -*- coding: utf-8 -*-
"""场景法 — 通过分析业务流程的基本流和备选流，设计端到端场景测试用例"""

from typing import Optional
from .base import BaseMethod, TestCase, MethodCategory


class ScenarioMethod(BaseMethod):
    """场景法

    分析需求中的业务流程描述，提取基本流和备选流，
    生成：基本流场景 + 每个备选流场景 + 备选流组合场景。
    """

    method_name: str = "scenario_method"
    method_category: MethodCategory = MethodCategory.BLACK_BOX
    description: str = "通过分析业务流程的基本流和备选流，设计端到端场景测试用例"
    applicable_scenarios: list[str] = ["业务流程", "购物流程", "注册流程", "审批流程", "端到端场景"]

    # ── analyze ──────────────────────────────────────────────
    def analyze(self, requirement: dict, context: Optional[dict] = None) -> dict:
        """分析需求中的基本流和备选流

        Args:
            requirement: {
                title, description,
                basic_flow: [{step, action, desc, target, value}, ...],
                alternative_flows: [{name, insertion_point, steps: [{action, desc, ...}]}, ...],
            }

        Returns:
            {
                basic_flow: [{step, action, desc, ...}],
                alternative_flows: [{name, insertion_point, steps}],
                scenarios: [{name, type, flows_used, description}],
                flow_count,
            }
        """
        basic_flow = requirement.get("basic_flow", [])
        alternative_flows = requirement.get("alternative_flows", [])

        # 如果没有显式 basic_flow，尝试从 description 中提取
        if not basic_flow:
            basic_flow = self._extract_basic_flow(requirement)

        if not basic_flow:
            return {
                "basic_flow": [],
                "alternative_flows": [],
                "scenarios": [],
                "flow_count": 0,
                "warning": "未找到基本流定义",
            }

        # 规范化基本流（兼容字符串和 dict 两种格式）
        normalized_basic = []
        for i, step in enumerate(basic_flow):
            if isinstance(step, str):
                normalized_basic.append({
                    "step": i + 1,
                    "action": "click",
                    "target": "",
                    "value": "",
                    "desc": step,
                })
            else:
                normalized_basic.append({
                    "step": step.get("step", i + 1),
                    "action": step.get("action", "click"),
                    "target": step.get("target", ""),
                    "value": step.get("value", ""),
                    "desc": step.get("desc", step.get("description", f"步骤 {i + 1}")),
                })

        # 规范化备选流（兼容字符串和 dict 两种格式）
        normalized_alternatives = []
        for i, alt in enumerate(alternative_flows):
            alt_steps = []
            for j, s in enumerate(alt.get("steps", [])):
                if isinstance(s, str):
                    alt_steps.append({
                        "step": j + 1,
                        "action": "click",
                        "target": "",
                        "value": "",
                        "desc": s,
                    })
                else:
                    alt_steps.append({
                        "step": s.get("step", j + 1),
                        "action": s.get("action", "click"),
                        "target": s.get("target", ""),
                        "value": s.get("value", ""),
                        "desc": s.get("desc", s.get("description", f"备选步骤 {j + 1}")),
                    })
            normalized_alternatives.append({
                "name": alt.get("name", f"备选流{i + 1}"),
                "insertion_point": alt.get("insertion_point", len(normalized_basic)),
                "steps": alt_steps,
                "result": alt.get("result", "备选流程结束"),
            })

        # 构建场景
        scenarios = self._build_scenarios(normalized_basic, normalized_alternatives)

        return {
            "basic_flow": normalized_basic,
            "alternative_flows": normalized_alternatives,
            "scenarios": scenarios,
            "flow_count": len(scenarios),
        }

    def _extract_basic_flow(self, requirement: dict) -> list:
        """从 description 或 rules 中提取基本流"""
        description = requirement.get("description", "")
        if not description:
            return []

        # 尝试解析步骤描述，如 "1. 打开页面 2. 输入用户名 3. 点击登录"
        import re
        steps = []
        pattern = r'(\d+)[\.\、\)]\s*(.+?)(?=(?:\d+[\.\、\)])|$)'
        matches = re.findall(pattern, description)
        for num, content in matches:
            content = content.strip().rstrip("。，,;；")
            if content:
                # 推断动作类型
                action = "click"
                if any(kw in content for kw in ["输入", "填写", "填入"]):
                    action = "fill"
                elif any(kw in content for kw in ["选择", "下拉"]):
                    action = "select_option"
                elif any(kw in content for kw in ["打开", "进入", "访问"]):
                    action = "navigate"
                elif any(kw in content for kw in ["勾选", "选中"]):
                    action = "check"
                elif any(kw in content for kw in ["上传"]):
                    action = "upload_file"
                elif any(kw in content for kw in ["等待", "加载"]):
                    action = "wait"

                steps.append({
                    "step": int(num),
                    "action": action,
                    "target": "",
                    "value": "",
                    "desc": content,
                })

        return steps

    def _build_scenarios(self, basic_flow: list, alternative_flows: list) -> list:
        """构建场景列表：基本流 + 每个备选流单独 + 组合"""
        scenarios = []

        # 场景1：基本流（快乐路径）
        scenarios.append({
            "name": "基本流-快乐路径",
            "type": "basic",
            "flows_used": ["basic"],
            "description": "正常业务流程，无任何异常",
            "priority": "high",
        })

        # 场景2~N：基本流 + 单个备选流
        for alt in alternative_flows:
            scenarios.append({
                "name": f"备选流: {alt['name']}",
                "type": "alternative",
                "flows_used": ["basic", alt["name"]],
                "description": f"基本流执行到步骤 {alt['insertion_point']} 后进入备选流: {alt['name']}",
                "priority": "high",
                "alternative_name": alt["name"],
                "insertion_point": alt["insertion_point"],
            })

        # 场景：基本流 + 多个备选流组合（如果备选流 >= 2）
        if len(alternative_flows) >= 2:
            for i in range(len(alternative_flows)):
                for j in range(i + 1, len(alternative_flows)):
                    alt_a = alternative_flows[i]
                    alt_b = alternative_flows[j]
                    scenarios.append({
                        "name": f"组合备选流: {alt_a['name']} + {alt_b['name']}",
                        "type": "combined",
                        "flows_used": ["basic", alt_a["name"], alt_b["name"]],
                        "description": f"基本流 + 备选流 {alt_a['name']} + 备选流 {alt_b['name']}",
                        "priority": "medium",
                        "alternative_names": [alt_a["name"], alt_b["name"]],
                        "insertion_points": [alt_a["insertion_point"], alt_b["insertion_point"]],
                    })

        return scenarios

    # ── generate_cases ───────────────────────────────────────
    def generate_cases(self, analysis: dict, filtered_taxonomy) -> list[TestCase]:
        """根据场景列表生成测试用例

        Args:
            analysis: analyze() 的返回值
            filtered_taxonomy: FilteredTaxonomy 实例

        Returns:
            list[TestCase]
        """
        basic_flow = analysis.get("basic_flow", [])
        alternative_flows = analysis.get("alternative_flows", [])
        scenarios = analysis.get("scenarios", [])

        actions = getattr(filtered_taxonomy, "actions", []) or []
        assertions = getattr(filtered_taxonomy, "assertions", []) or []

        if not actions:
            actions = ["navigate", "click", "fill", "wait", "scroll"]
        if not assertions:
            assertions = ["text_contains", "url_matches", "element_exists", "element_visible", "toast"]

        cases = []
        alt_map = {alt["name"]: alt for alt in alternative_flows}

        for idx, scenario in enumerate(scenarios):
            case_id = f"SM-{idx + 1:03d}"
            steps = []
            flow_names = scenario.get("flows_used", [])
            sc_type = scenario.get("type", "basic")

            if sc_type == "basic":
                # 纯基本流
                steps = self._flow_to_steps(basic_flow, actions)
            elif sc_type == "alternative":
                # 基本流 + 单个备选流
                alt_name = scenario.get("alternative_name", "")
                insertion = scenario.get("insertion_point", len(basic_flow))
                alt_info = alt_map.get(alt_name, {})

                # 基本流前半段
                for step in basic_flow:
                    if step["step"] < insertion:
                        steps.append(self._step_to_dict(step, actions))
                    else:
                        break

                # 备选流步骤
                for step in alt_info.get("steps", []):
                    steps.append(self._step_to_dict(step, actions))

                # 结果验证
                steps.append({
                    "action": "wait",
                    "target": "#result",
                    "value": alt_info.get("result", ""),
                    "desc": f"等待备选流结果: {alt_info.get('result', '')}",
                })
            elif sc_type == "combined":
                # 基本流 + 多个备选流组合
                alt_names = scenario.get("alternative_names", [])
                insertion_points = scenario.get("insertion_points", [])
                # 按 insertion_point 排序以确定执行顺序
                alt_tuples = sorted(zip(insertion_points, alt_names), key=lambda x: x[0])
                sorted_insertions = [p for p, _ in alt_tuples]
                sorted_alt_names = [n for _, n in alt_tuples]

                current_step = 0
                for alt_name, ins_point in zip(sorted_alt_names, sorted_insertions):
                    alt_info = alt_map.get(alt_name, {})
                    # 基本流从 current_step 到 ins_point
                    for step in basic_flow:
                        if current_step <= step["step"] < ins_point:
                            steps.append(self._step_to_dict(step, actions))
                    current_step = ins_point
                    # 备选流步骤
                    for step in alt_info.get("steps", []):
                        steps.append(self._step_to_dict(step, actions))
                # 剩余基本流步骤
                for step in basic_flow:
                    if step["step"] >= current_step:
                        steps.append(self._step_to_dict(step, actions))

            # 生成断言
            case_assertions = []
            for atype in assertions:
                case_assertions.append({
                    "type": atype,
                    "target": "#page",
                    "desc": f"验证场景 '{scenario['name']}' 执行结果（{atype}）",
                })

            case_name = f"场景: {scenario['name']}"
            precondition = f"系统处于初始状态，准备执行业务流程"

            cases.append(TestCase(
                case_id=case_id,
                name=case_name,
                method=self.method_name,
                category=self.method_category.value,
                priority=scenario.get("priority", "medium"),
                module="",
                precondition=precondition,
                test_data={"scenario_type": sc_type, "flows": flow_names},
                steps=steps,
                assertions=case_assertions,
                coverage_dimension=f"场景覆盖: {', '.join(flow_names)}",
                tags=["scenario_method", sc_type] + flow_names,
            ))

        return cases

    def _flow_to_steps(self, flow: list, actions: list[str]) -> list[dict]:
        """将流程步骤列表转换为操作步骤"""
        return [self._step_to_dict(s, actions) for s in flow]

    def _step_to_dict(self, step: dict, actions: list[str]) -> dict:
        """将单个流程步骤转换为操作步骤字典"""
        action = step.get("action", "click")
        # 确保 action 在可用操作列表中
        if action not in actions:
            action = actions[0] if actions else "click"

        return {
            "action": action,
            "target": step.get("target", ""),
            "value": str(step.get("value", "")),
            "desc": step.get("desc", f"执行操作: {action}"),
        }