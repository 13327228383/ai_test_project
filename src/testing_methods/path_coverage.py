# -*- coding: utf-8 -*-
"""路径覆盖 (Path Coverage) — 白盒测试方法

覆盖程序中所有可能的执行路径，确保每个路径组合都被测试。
"""

import re
import logging
from typing import Optional

from .base import BaseMethod, TestCase, MethodResult, MethodCategory

_log = logging.getLogger(__name__)


class PathCoverage(BaseMethod):
    """路径覆盖：覆盖所有可达的页面导航路径和业务流程路径"""

    method_name: str = "path_coverage"
    method_category: MethodCategory = MethodCategory.WHITE_BOX
    description: str = "覆盖程序中所有可能的执行路径，确保每个路径组合都被测试"
    applicable_scenarios: list[str] = [
        "页面导航路径", "业务流程路径", "多步骤流程",
        "循环路径覆盖", "工作流路径", "端到端路径",
    ]

    # ── 分析阶段 ──────────────────────────────────────────────────────────

    def analyze(self, requirement: dict, context: Optional[dict] = None) -> dict:
        """分析需求中的页面导航路径或业务流程路径。

        Args:
            requirement: {
                title, description,
                page_structure: {elements: [...], navigation: [...]},
                workflow: {nodes: [{id, name, transitions: [{target, action, condition}]}], ...},
                code_paths: [{path: [...], description, ...}],
                ...
            }
            context: 额外上下文

        Returns:
            {
                paths: [{id, type, nodes: [{id, name, action}], edges: [{from, to, action}], description}],
                path_types: {navigation: 3, workflow: 2, loop: 1, ...},
                total_paths: int,
                total_nodes: int,
                source: "code" | "workflow" | "page_structure" | "simulated",
            }
        """
        workflow = requirement.get("workflow", {})
        page_structure = requirement.get("page_structure", {})
        code_paths = requirement.get("code_paths", [])
        paths = []
        source = "simulated"

        # 1. 从代码路径中提取
        if code_paths:
            source = "code"
            for idx, cp in enumerate(code_paths):
                path_nodes = cp.get("path", cp.get("nodes", []))
                paths.append({
                    "id": f"PATH-{idx + 1:03d}",
                    "type": cp.get("type", "code_path"),
                    "nodes": [
                        self._make_node(i, n) if isinstance(n, str) else n
                        for i, n in enumerate(path_nodes)
                    ],
                    "edges": self._build_edges_from_nodes(path_nodes),
                    "description": cp.get("description", f"Code path {idx + 1}"),
                    "source": "code",
                })

        # 2. 从工作流中提取路径
        if workflow:
            if source == "simulated":
                source = "workflow"
            else:
                source = "mixed"
            workflow_paths = self._extract_workflow_paths(workflow, len(paths))
            paths.extend(workflow_paths)

        # 3. 从页面结构导航中提取路径
        navigation = page_structure.get("navigation", []) if page_structure else []
        if navigation:
            if source == "simulated":
                source = "page_structure"
            else:
                source = "mixed"
            nav_paths = self._extract_navigation_paths(navigation, len(paths))
            paths.extend(nav_paths)

        # 4. 从需求描述中模拟路径
        if not paths:
            source = "simulated"
            paths = self._simulate_paths_from_description(requirement)

        # 统计
        path_types = {}
        total_nodes = 0
        for p in paths:
            pt = p.get("type", "unknown")
            path_types[pt] = path_types.get(pt, 0) + 1
            total_nodes += len(p.get("nodes", []))

        _log.info("PathCoverage.analyze: %d paths, %d nodes (source=%s)",
                   len(paths), total_nodes, source)

        return {
            "paths": paths,
            "path_types": path_types,
            "total_paths": len(paths),
            "total_nodes": total_nodes,
            "source": source,
        }

    def _make_node(self, index: int, name: str) -> dict:
        """从字符串创建节点"""
        return {"id": f"N{index + 1}", "name": name, "action": ""}

    def _build_edges_from_nodes(self, nodes: list) -> list[dict]:
        """从节点列表构建边"""
        edges = []
        for i in range(len(nodes) - 1):
            from_node = nodes[i] if isinstance(nodes[i], str) else nodes[i].get("id", nodes[i].get("name", ""))
            to_node = nodes[i + 1] if isinstance(nodes[i + 1], str) else nodes[i + 1].get("id", nodes[i + 1].get("name", ""))
            edges.append({
                "from": from_node,
                "to": to_node,
                "action": "",
            })
        return edges

    def _extract_workflow_paths(self, workflow: dict, start_idx: int) -> list[dict]:
        """从工作流定义中提取所有可达路径"""
        nodes = workflow.get("nodes", [])
        if not nodes:
            return []

        paths = []
        node_map = {n.get("id", ""): n for n in nodes}

        # 找到起始节点
        start_nodes = workflow.get("start_nodes", [])
        if not start_nodes:
            # 找没有入边的节点
            all_targets = set()
            for n in nodes:
                for t in n.get("transitions", []):
                    all_targets.add(t.get("target", ""))
            start_nodes = [n["id"] for n in nodes if n["id"] not in all_targets]
            if not start_nodes and nodes:
                start_nodes = [nodes[0]["id"]]

        # DFS 遍历所有路径（带循环检测和最大深度限制）
        MAX_DEPTH = 8  # 限制循环展开次数
        visited_paths = set()

        def dfs(current_id: str, path_nodes: list[dict], path_edges: list[dict], depth: int):
            if depth > MAX_DEPTH:
                return

            current = node_map.get(current_id)
            if not current:
                return

            transitions = current.get("transitions", [])

            if not transitions:
                # 叶子节点，记录路径
                path_key = "->".join(n.get("id", n.get("name", "")) for n in path_nodes)
                if path_key not in visited_paths:
                    visited_paths.add(path_key)
                    paths.append({
                        "id": f"PATH-{start_idx + len(paths) + 1:03d}",
                        "type": "workflow",
                        "nodes": list(path_nodes),
                        "edges": list(path_edges),
                        "description": f"工作流路径: {' -> '.join(n.get('name', n.get('id', '')) for n in path_nodes)}",
                        "source": "workflow",
                    })
                return

            for trans in transitions:
                target_id = trans.get("target", "")
                if not target_id:
                    continue

                target_node = node_map.get(target_id)
                if not target_node:
                    continue

                new_node = {
                    "id": target_node.get("id", target_id),
                    "name": target_node.get("name", target_id),
                    "action": trans.get("action", ""),
                }
                new_edge = {
                    "from": current_id,
                    "to": target_id,
                    "action": trans.get("action", ""),
                    "condition": trans.get("condition", ""),
                }

                # 循环检测：如果目标节点已在路径中，记录后继续（限制深度）
                cycle_count = sum(1 for n in path_nodes if n["id"] == target_id)
                if cycle_count >= 2:
                    # 循环超过2次，记录路径后停止
                    final_nodes = list(path_nodes) + [new_node]
                    final_edges = list(path_edges) + [new_edge]
                    path_key = "->".join(n.get("id", "") for n in final_nodes)
                    if path_key not in visited_paths:
                        visited_paths.add(path_key)
                        paths.append({
                            "id": f"PATH-{start_idx + len(paths) + 1:03d}",
                            "type": "workflow_loop",
                            "nodes": final_nodes,
                            "edges": final_edges,
                            "description": f"工作流循环路径({cycle_count + 1}次): {' -> '.join(n.get('name', n.get('id', '')) for n in final_nodes)}",
                            "source": "workflow",
                        })
                    continue

                dfs(target_id, path_nodes + [new_node], path_edges + [new_edge], depth + 1)

        for start_id in start_nodes:
            start_node = node_map.get(start_id)
            if start_node:
                dfs(start_id, [{
                    "id": start_node.get("id", start_id),
                    "name": start_node.get("name", start_id),
                    "action": "",
                }], [], 1)

        return paths

    def _extract_navigation_paths(self, navigation: list[dict], start_idx: int) -> list[dict]:
        """从页面导航定义中提取导航路径"""
        paths = []
        for idx, nav in enumerate(navigation):
            route = nav.get("route", nav.get("path", []))
            if isinstance(route, list) and len(route) >= 2:
                nodes = []
                for i, page in enumerate(route):
                    if isinstance(page, str):
                        nodes.append({"id": f"P{i + 1}", "name": page, "action": "navigate"})
                    else:
                        nodes.append({
                            "id": page.get("id", f"P{i + 1}"),
                            "name": page.get("name", page.get("page", "")),
                            "action": page.get("action", "navigate"),
                        })
                paths.append({
                    "id": f"PATH-{start_idx + idx + 1:03d}",
                    "type": "navigation",
                    "nodes": nodes,
                    "edges": self._build_edges_from_nodes(nodes),
                    "description": nav.get("description", f"导航路径: {' -> '.join(n['name'] for n in nodes)}"),
                    "source": "navigation",
                })

        return paths

    def _simulate_paths_from_description(self, requirement: dict) -> list[dict]:
        """根据需求描述模拟业务路径"""
        description = requirement.get("description", "") + " " + requirement.get("title", "")
        paths = []

        # 模拟常见业务流程路径
        simulated_paths = [
            {
                "pattern": r"登录|注册|认证",
                "type": "authentication",
                "nodes": [
                    {"id": "N1", "name": "访问首页", "action": "navigate"},
                    {"id": "N2", "name": "点击登录/注册", "action": "click"},
                    {"id": "N3", "name": "填写凭证", "action": "fill"},
                    {"id": "N4", "name": "提交表单", "action": "click"},
                    {"id": "N5", "name": "进入主页", "action": "wait"},
                ],
                "desc": "用户认证流程路径",
            },
            {
                "pattern": r"搜索|查询|检索",
                "type": "search",
                "nodes": [
                    {"id": "N1", "name": "进入搜索页面", "action": "navigate"},
                    {"id": "N2", "name": "输入搜索关键词", "action": "fill"},
                    {"id": "N3", "name": "点击搜索", "action": "click"},
                    {"id": "N4", "name": "查看搜索结果", "action": "scroll"},
                    {"id": "N5", "name": "点击结果项", "action": "click"},
                    {"id": "N6", "name": "查看详情页", "action": "wait"},
                ],
                "desc": "搜索流程路径",
            },
            {
                "pattern": r"购买|下单|购物|支付|订单",
                "type": "purchase",
                "nodes": [
                    {"id": "N1", "name": "浏览商品", "action": "scroll"},
                    {"id": "N2", "name": "加入购物车", "action": "click"},
                    {"id": "N3", "name": "进入购物车", "action": "navigate"},
                    {"id": "N4", "name": "确认订单", "action": "click"},
                    {"id": "N5", "name": "填写地址", "action": "fill"},
                    {"id": "N6", "name": "选择支付方式", "action": "select_option"},
                    {"id": "N7", "name": "确认支付", "action": "click"},
                    {"id": "N8", "name": "查看支付结果", "action": "wait"},
                ],
                "desc": "购买流程路径",
            },
            {
                "pattern": r"审批|审核|流程|工作流",
                "type": "approval",
                "nodes": [
                    {"id": "N1", "name": "提交申请", "action": "click"},
                    {"id": "N2", "name": "填写申请信息", "action": "fill"},
                    {"id": "N3", "name": "提交审核", "action": "click"},
                    {"id": "N4", "name": "等待审批", "action": "wait"},
                    {"id": "N5", "name": "审批通过", "action": "click"},
                    {"id": "N6", "name": "查看审批结果", "action": "wait"},
                ],
                "desc": "审批流程路径",
            },
            {
                "pattern": r"新增|创建|添加|新建",
                "type": "create",
                "nodes": [
                    {"id": "N1", "name": "进入列表页", "action": "navigate"},
                    {"id": "N2", "name": "点击新增按钮", "action": "click"},
                    {"id": "N3", "name": "填写表单", "action": "fill"},
                    {"id": "N4", "name": "提交保存", "action": "click"},
                    {"id": "N5", "name": "返回列表页", "action": "go_back"},
                    {"id": "N6", "name": "验证新增记录", "action": "scroll"},
                ],
                "desc": "新增记录流程路径",
            },
            {
                "pattern": r"编辑|修改|更新",
                "type": "edit",
                "nodes": [
                    {"id": "N1", "name": "进入列表页", "action": "navigate"},
                    {"id": "N2", "name": "点击编辑按钮", "action": "click"},
                    {"id": "N3", "name": "修改字段值", "action": "fill"},
                    {"id": "N4", "name": "保存修改", "action": "click"},
                    {"id": "N5", "name": "验证修改结果", "action": "wait"},
                ],
                "desc": "编辑记录流程路径",
            },
            {
                "pattern": r"删除|移除|清除",
                "type": "delete",
                "nodes": [
                    {"id": "N1", "name": "进入列表页", "action": "navigate"},
                    {"id": "N2", "name": "点击删除按钮", "action": "click"},
                    {"id": "N3", "name": "确认删除", "action": "click"},
                    {"id": "N4", "name": "验证删除结果", "action": "wait"},
                ],
                "desc": "删除记录流程路径",
            },
        ]

        for sp in simulated_paths:
            if re.search(sp["pattern"], description):
                paths.append({
                    "id": f"PATH-SIM-{len(paths) + 1:03d}",
                    "type": sp["type"],
                    "nodes": sp["nodes"],
                    "edges": self._build_edges_from_nodes(sp["nodes"]),
                    "description": sp["desc"],
                    "source": "simulated",
                })

        # 如果没有匹配到任何路径，提供通用路径
        if not paths:
            paths.append({
                "id": "PATH-SIM-001",
                "type": "generic",
                "nodes": [
                    {"id": "N1", "name": "页面入口", "action": "navigate"},
                    {"id": "N2", "name": "主要操作", "action": "click"},
                    {"id": "N3", "name": "结果确认", "action": "wait"},
                ],
                "edges": [
                    {"from": "N1", "to": "N2", "action": "click"},
                    {"from": "N2", "to": "N3", "action": "wait"},
                ],
                "description": "通用业务流程路径",
                "source": "simulated",
            })

        return paths

    # ── 用例生成阶段 ──────────────────────────────────────────────────────

    def generate_cases(self, analysis: dict, filtered_taxonomy) -> list[TestCase]:
        """为每条路径生成覆盖用例，包括循环路径的关键次数。

        Args:
            analysis: self.analyze() 的返回值
            filtered_taxonomy: FilteredTaxonomy 实例

        Returns:
            list[TestCase]
        """
        paths = analysis.get("paths", [])
        available_actions = filtered_taxonomy.actions if hasattr(filtered_taxonomy, 'actions') else filtered_taxonomy.get("actions", [])
        available_assertions = filtered_taxonomy.assertions if hasattr(filtered_taxonomy, 'assertions') else filtered_taxonomy.get("assertions", [])

        if not paths:
            _log.warning("PathCoverage: no paths to cover")
            return []

        cases = []
        for idx, path in enumerate(paths):
            case_id = f"PC-{idx + 1:03d}"
            case = self._build_path_case(
                case_id=case_id,
                path=path,
                index=idx + 1,
                actions=available_actions,
                assertions=available_assertions,
            )
            cases.append(case)

        _log.info("PathCoverage.generate_cases: %d cases from %d paths",
                   len(cases), len(paths))
        return cases

    def _build_path_case(self, case_id: str, path: dict, index: int,
                          actions: list[str], assertions: list[str]) -> TestCase:
        """为单条路径构建测试用例"""
        path_type = path.get("type", "unknown")
        nodes = path.get("nodes", [])
        edges = path.get("edges", [])
        description = path.get("description", "")

        # 构建步骤序列
        steps = []
        for node in nodes:
            node_action = node.get("action", "")
            node_name = node.get("name", node.get("id", ""))

            # 选择操作：优先使用节点定义的操作，否则使用可用操作
            action = node_action if node_action in actions else self._pick_path_action(node_name, actions)
            steps.append({
                "action": action,
                "target": node_name,
                "value": "",
                "desc": f"步骤: {node_name}",
            })

        # 构建断言
        assertion_list = self._build_path_assertions(path, assertions)

        # 优先级
        priority = self._determine_path_priority(path_type)

        # 路径名称
        node_names = [n.get("name", n.get("id", "")) for n in nodes]
        name = f"路径覆盖-{path_type}({' -> '.join(node_names)})"

        return TestCase(
            case_id=case_id,
            name=name[:80],  # 限制长度
            method=self.method_name,
            category=self.method_category.value,
            priority=priority,
            module=path_type,
            precondition="系统处于初始状态，所有前置条件已满足",
            test_data={
                "path_type": path_type,
                "path_nodes": node_names,
                "path_description": description,
                "node_count": len(nodes),
                "edge_count": len(edges),
            },
            steps=steps,
            assertions=assertion_list,
            coverage_dimension=f"路径覆盖-{path_type}-{path['id']}",
            tags=["path_coverage", path_type, "白盒"],
        )

    def _pick_path_action(self, node_name: str, available_actions: list[str]) -> str:
        """根据节点名称选择合适的操作"""
        name_lower = node_name.lower()

        if any(kw in name_lower for kw in ["点击", "click", "按钮", "button", "提交", "确认", "删除"]):
            return "click" if "click" in available_actions else (available_actions[0] if available_actions else "click")

        if any(kw in name_lower for kw in ["填写", "输入", "input", "fill", "表单", "地址", "关键词"]):
            return "fill" if "fill" in available_actions else "type_text" if "type_text" in available_actions else (available_actions[0] if available_actions else "click")

        if any(kw in name_lower for kw in ["选择", "select", "下拉", "支付方式"]):
            return "select_option" if "select_option" in available_actions else (available_actions[0] if available_actions else "click")

        if any(kw in name_lower for kw in ["导航", "navigate", "进入", "访问", "页面", "首页"]):
            return "navigate" if "navigate" in available_actions else (available_actions[0] if available_actions else "click")

        if any(kw in name_lower for kw in ["等待", "wait", "加载", "查看", "结果"]):
            return "wait" if "wait" in available_actions else (available_actions[0] if available_actions else "click")

        if any(kw in name_lower for kw in ["返回", "back", "go_back"]):
            return "go_back" if "go_back" in available_actions else (available_actions[0] if available_actions else "click")

        if any(kw in name_lower for kw in ["滚动", "scroll", "浏览"]):
            return "scroll" if "scroll" in available_actions else (available_actions[0] if available_actions else "click")

        return available_actions[0] if available_actions else "click"

    def _build_path_assertions(self, path: dict, available_assertions: list[str]) -> list[dict]:
        """为路径构建断言列表"""
        assertions = []
        path_type = path.get("type", "unknown")
        nodes = path.get("nodes", [])

        # 路径起始：验证入口
        if "url_matches" in available_assertions:
            assertions.append({
                "type": "url_matches",
                "target": nodes[0].get("name", "") if nodes else "",
                "desc": "验证起始页面URL正确",
            })

        # 关键节点：验证元素存在
        if nodes and "element_exists" in available_assertions:
            # 验证中间节点
            mid_idx = len(nodes) // 2
            if mid_idx < len(nodes):
                assertions.append({
                    "type": "element_exists",
                    "target": nodes[mid_idx].get("name", ""),
                    "desc": f"验证关键节点 '{nodes[mid_idx].get('name', '')}' 元素存在",
                })

        # 路径终点：验证结果
        if nodes and len(nodes) >= 2:
            final_node = nodes[-1]
            if "text_contains" in available_assertions:
                assertions.append({
                    "type": "text_contains",
                    "target": "result-indicator",
                    "desc": f"验证最终节点 '{final_node.get('name', '')}' 结果正确",
                })
            elif "element_exists" in available_assertions:
                assertions.append({
                    "type": "element_exists",
                    "target": final_node.get("name", ""),
                    "desc": f"验证最终节点 '{final_node.get('name', '')}' 存在",
                })

        # 路径类型特定断言
        if path_type == "purchase" and "url_matches" in available_assertions:
            assertions.append({
                "type": "url_matches",
                "target": "payment-result",
                "desc": "验证支付结果页面URL正确",
            })

        if path_type == "approval" and "text_contains" in available_assertions:
            assertions.append({
                "type": "text_contains",
                "target": "approval-result",
                "desc": "验证审批结果提示",
            })

        if "no_error" in available_assertions:
            assertions.append({
                "type": "no_error",
                "target": "",
                "desc": "验证整个路径执行过程中无控制台错误",
            })

        if "loading_complete" in available_assertions:
            assertions.append({
                "type": "loading_complete",
                "target": "",
                "desc": "验证路径中所有页面加载完成",
            })

        return assertions

    def _determine_path_priority(self, path_type: str) -> str:
        """根据路径类型确定优先级"""
        high_priority = {"purchase", "payment", "approval", "authentication", "workflow"}
        medium_priority = {"create", "edit", "delete", "search", "workflow_loop"}
        if path_type in high_priority:
            return "high"
        elif path_type in medium_priority:
            return "medium"
        return "low"


# ── 独立运行测试 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    from .taxonomy_filter import FilteredTaxonomy

    # 模拟需求数据
    requirement = {
        "title": "订单审批流程",
        "description": "用户提交订单后，需要经过部门经理审批，审批通过后通知财务。支持审批驳回和重新提交。",
        "page_structure": {
            "elements": [
                {"tag": "button", "id": "btn-submit", "text": "提交订单"},
                {"tag": "button", "id": "btn-approve", "text": "审批通过"},
                {"tag": "button", "id": "btn-reject", "text": "驳回"},
                {"tag": "button", "id": "btn-resubmit", "text": "重新提交"},
            ],
        },
        "workflow": {
            "start_nodes": ["draft"],
            "nodes": [
                {
                    "id": "draft", "name": "草稿",
                    "transitions": [{"target": "pending", "action": "提交", "condition": ""}],
                },
                {
                    "id": "pending", "name": "待审批",
                    "transitions": [
                        {"target": "approved", "action": "审批通过", "condition": "has_permission"},
                        {"target": "rejected", "action": "驳回", "condition": "has_permission"},
                    ],
                },
                {
                    "id": "rejected", "name": "已驳回",
                    "transitions": [{"target": "pending", "action": "重新提交", "condition": ""}],
                },
                {
                    "id": "approved", "name": "已通过",
                    "transitions": [{"target": "completed", "action": "通知财务", "condition": ""}],
                },
                {
                    "id": "completed", "name": "已完成",
                    "transitions": [],
                },
            ],
        },
    }

    ft = FilteredTaxonomy(
        actions=["navigate", "click", "fill", "select_option", "go_back", "wait", "scroll"],
        assertions=["text_contains", "url_matches", "element_exists",
                     "no_error", "loading_complete", "element_visible"],
    )

    method = PathCoverage()
    print(f"=== {method.method_name} ===")
    print(f"Category: {method.method_category.value}")
    print(f"Description: {method.description}")

    analysis = method.analyze(requirement)
    print(f"\nAnalysis: {analysis['total_paths']} paths, {analysis['total_nodes']} nodes, source={analysis['source']}")
    print(f"Path types: {analysis['path_types']}")
    for p in analysis["paths"]:
        node_names = [n.get("name", n.get("id", "")) for n in p["nodes"]]
        print(f"  [{p['id']}] {p['type']}: {' -> '.join(node_names)}")

    cases = method.generate_cases(analysis, ft)
    print(f"\nGenerated {len(cases)} test cases:")
    for c in cases:
        print(f"  [{c.case_id}] {c.name[:70]} | steps={len(c.steps)} | assertions={len(c.assertions)}")

    result = method.run(requirement, ft)
    print(f"\nResult: {result.total_cases} cases, coverage={result.coverage_report}")