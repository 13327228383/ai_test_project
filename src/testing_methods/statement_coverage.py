# -*- coding: utf-8 -*-
"""语句覆盖 (Statement Coverage) — 白盒测试方法

确保每个页面元素/代码语句至少被执行一次，达到最基本的覆盖。
"""

import re
import logging
from typing import Optional

from .base import BaseMethod, TestCase, MethodResult, MethodCategory

_log = logging.getLogger(__name__)


class StatementCoverage(BaseMethod):
    """语句覆盖：确保每个页面可交互元素/代码语句至少被操作一次"""

    method_name: str = "statement_coverage"
    method_category: MethodCategory = MethodCategory.WHITE_BOX
    description: str = "确保每个页面元素/代码语句至少被执行一次，达到最基本的覆盖"
    applicable_scenarios: list[str] = [
        "页面元素覆盖", "代码语句覆盖", "最小功能验证",
        "冒烟测试", "回归测试", "全量覆盖",
    ]

    # ── 分析阶段 ──────────────────────────────────────────────────────────

    def analyze(self, requirement: dict, context: Optional[dict] = None) -> dict:
        """分析需求中的 page_structure / code_branches，提取所有可交互元素。

        Args:
            requirement: {
                title, description,
                page_structure: {elements: [{tag, type, id, class, text, role, ...}], ...},
                code_branches: [{type, condition, location, ...}],  # 可选
                ...
            }
            context: 额外上下文

        Returns:
            {
                statements: [{id, type, tag, description, element_ref, category}],
                categories: {button: 3, link: 5, input: 2, ...},
                total_statements: int,
                code_statements: [{id, location, description}],  # 如果有代码
                source: "page_structure" | "code" | "simulated",
            }
        """
        page_structure = requirement.get("page_structure", {})
        code_branches = requirement.get("code_branches", [])
        elements = page_structure.get("elements", []) if page_structure else []

        statements = []
        code_statements = []
        source = "simulated"

        # 优先从代码分支中提取语句
        if code_branches:
            source = "code"
            for idx, branch in enumerate(code_branches):
                stmt_id = f"STMT-{idx + 1:03d}"
                statements.append({
                    "id": stmt_id,
                    "type": "code_statement",
                    "tag": "code",
                    "description": branch.get("description", f"Code statement at {branch.get('location', 'unknown')}"),
                    "element_ref": branch.get("location", ""),
                    "category": "code",
                })
                code_statements.append({
                    "id": stmt_id,
                    "location": branch.get("location", ""),
                    "description": branch.get("description", ""),
                })

        # 从页面结构中提取可交互元素
        if elements:
            source = "page_structure" if source == "simulated" else "mixed"
            tag_map = {
                "button": "button", "a": "link", "input": "input",
                "select": "select", "textarea": "textarea",
                "form": "form", "table": "table",
                "img": "image", "iframe": "iframe",
                "span": "text", "div": "container",
                "li": "list_item", "td": "cell", "tr": "row",
                "nav": "navigation", "ul": "list", "ol": "list",
            }

            start_idx = len(statements)
            for idx, el in enumerate(elements):
                tag = el.get("tag", "").lower()
                el_type = tag_map.get(tag, tag)
                el_id = el.get("id", "") or el.get("class", "") or ""
                el_text = el.get("text", "") or el.get("label", "") or ""
                el_role = el.get("role", "") or ""

                # 只关注可交互元素
                if tag in ("button", "a", "input", "select", "textarea", "iframe"):
                    stmt_id = f"STMT-{start_idx + idx + 1:03d}"
                    desc_parts = []
                    if el_text:
                        desc_parts.append(f"'{el_text}'")
                    if el_id:
                        desc_parts.append(f"[{el_id}]")
                    if el_role:
                        desc_parts.append(f"role={el_role}")
                    desc = f"{tag} {' '.join(desc_parts)}" if desc_parts else f"{tag} element"

                    statements.append({
                        "id": stmt_id,
                        "type": el_type,
                        "tag": tag,
                        "description": desc,
                        "element_ref": el_id or el_text or f"element-{idx}",
                        "category": "page_element",
                    })

        # 如果既没有页面结构也没有代码，基于需求描述模拟
        if not statements:
            source = "simulated"
            statements = self._simulate_from_description(requirement)

        # 统计分类
        categories = {}
        for s in statements:
            cat = s.get("category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1

        _log.info("StatementCoverage.analyze: %d statements found (source=%s)", len(statements), source)

        return {
            "statements": statements,
            "categories": categories,
            "total_statements": len(statements),
            "code_statements": code_statements,
            "source": source,
        }

    def _simulate_from_description(self, requirement: dict) -> list[dict]:
        """根据需求描述文本模拟页面元素"""
        description = requirement.get("description", "") + " " + requirement.get("title", "")
        statements = []
        idx = 0

        # 模拟常见页面元素
        patterns = [
            (r"按钮|button|提交|确认|取消|保存|删除|编辑|新增|添加|搜索|查询|登录|注册|退出|返回|下一步|上一步",
             "button", "button"),
            (r"链接|跳转|链接到|详情|查看|更多|帮助|关于|首页|导航",
             "link", "a"),
            (r"输入框|文本框|输入|填写|用户名|密码|邮箱|手机|电话|地址|姓名|搜索|关键词",
             "input", "input"),
            (r"下拉|选择|下拉框|下拉列表|选项|select",
             "select", "select"),
            (r"文本域|文本区|备注|描述|详细|说明|textarea",
             "textarea", "textarea"),
            (r"表单|form",
             "form", "form"),
            (r"表格|列表|table",
             "table", "table"),
            (r"勾选|复选框|checkbox|多选|勾选框",
             "checkbox", "input"),
            (r"单选|radio|单选框",
             "radio", "input"),
            (r"上传|文件|附件|upload",
             "upload", "input"),
            (r"日期|时间|date|datetime",
             "date", "input"),
        ]

        for pattern, el_type, tag in patterns:
            matches = re.findall(pattern, description, re.IGNORECASE)
            if matches:
                idx += 1
                statements.append({
                    "id": f"STMT-{idx:03d}",
                    "type": el_type,
                    "tag": tag,
                    "description": f"Simulated {el_type}: {matches[0]}",
                    "element_ref": f"simulated-{el_type}-{idx}",
                    "category": "simulated",
                })

        # 至少保证有一些基础元素
        if not statements:
            for i, (el_type, tag) in enumerate([
                ("button", "button"), ("input", "input"), ("link", "a"),
                ("select", "select"), ("button", "button"),
            ]):
                idx += 1
                statements.append({
                    "id": f"STMT-{idx:03d}",
                    "type": el_type,
                    "tag": tag,
                    "description": f"Simulated {el_type} element #{i + 1}",
                    "element_ref": f"simulated-{el_type}-{idx}",
                    "category": "simulated",
                })

        return statements

    # ── 用例生成阶段 ──────────────────────────────────────────────────────

    def generate_cases(self, analysis: dict, filtered_taxonomy) -> list[TestCase]:
        """为每个语句/元素生成至少一个覆盖用例。

        Args:
            analysis: self.analyze() 的返回值
            filtered_taxonomy: FilteredTaxonomy 实例，有 actions 和 assertions 字段

        Returns:
            list[TestCase]
        """
        statements = analysis.get("statements", [])
        available_actions = filtered_taxonomy.actions if hasattr(filtered_taxonomy, 'actions') else filtered_taxonomy.get("actions", [])
        available_assertions = filtered_taxonomy.assertions if hasattr(filtered_taxonomy, 'assertions') else filtered_taxonomy.get("assertions", [])

        if not statements:
            _log.warning("StatementCoverage: no statements to cover")
            return []

        # 按分类分组，每组生成一个用例
        grouped = self._group_statements(statements)
        cases = []

        for group_idx, (category, group_stmts) in enumerate(grouped.items()):
            case_id = f"SC-{group_idx + 1:03d}"
            case = self._build_case_for_group(
                case_id=case_id,
                index=group_idx + 1,
                category=category,
                statements=group_stmts,
                actions=available_actions,
                assertions=available_assertions,
            )
            cases.append(case)

        # 如果语句较多，为每个语句单独生成用例（确保细化覆盖）
        if len(statements) > 10:
            single_cases = self._generate_single_statement_cases(
                statements, available_actions, available_assertions,
                start_index=len(grouped) + 1,
            )
            cases.extend(single_cases)

        # 去重：按 case_id 去重
        seen_ids = set()
        unique_cases = []
        for c in cases:
            if c.case_id not in seen_ids:
                seen_ids.add(c.case_id)
                unique_cases.append(c)

        _log.info("StatementCoverage.generate_cases: %d cases from %d statements",
                   len(unique_cases), len(statements))
        return unique_cases

    def _group_statements(self, statements: list[dict]) -> dict[str, list[dict]]:
        """按元素类型分组，每组共享一个用例"""
        grouped = {}
        for s in statements:
            cat = s.get("category", "unknown")
            grouped.setdefault(cat, []).append(s)
        return grouped

    def _build_case_for_group(self, case_id: str, index: int, category: str,
                               statements: list[dict], actions: list[str],
                               assertions: list[str]) -> TestCase:
        """为同一分类的语句组构建一个覆盖用例"""
        stmt_descs = [s["description"] for s in statements]
        name = f"语句覆盖-{category}({len(statements)}个元素)"

        steps = []
        for s in statements:
            action = self._pick_action(s["tag"], actions)
            steps.append({
                "action": action,
                "target": s.get("element_ref", ""),
                "value": "",
                "desc": f"操作{s['description']}",
            })

        # 断言：验证每个元素存在
        assertion_list = self._build_assertions(statements, assertions)

        return TestCase(
            case_id=case_id,
            name=name,
            method=self.method_name,
            category=self.method_category.value,
            priority="medium",
            module=category,
            precondition=f"页面已加载，{category}元素可见",
            test_data={"covered_elements": stmt_descs},
            steps=steps,
            assertions=assertion_list,
            coverage_dimension=f"语句覆盖-{category}",
            tags=["statement_coverage", category, "白盒"],
        )

    def _generate_single_statement_cases(self, statements: list[dict],
                                          actions: list[str],
                                          assertions: list[str],
                                          start_index: int) -> list[TestCase]:
        """为每个语句生成独立用例，确保细粒度覆盖"""
        cases = []
        for i, stmt in enumerate(statements):
            case_id = f"SC-{start_index + i:03d}"
            action = self._pick_action(stmt["tag"], actions)
            assertion_type = self._pick_assertion(assertions)

            cases.append(TestCase(
                case_id=case_id,
                name=f"语句覆盖-{stmt['description']}",
                method=self.method_name,
                category=self.method_category.value,
                priority="low",
                module=stmt.get("category", "unknown"),
                precondition="页面已加载完成",
                test_data={"element": stmt["description"]},
                steps=[{
                    "action": action,
                    "target": stmt.get("element_ref", ""),
                    "value": "",
                    "desc": f"操作{stmt['description']}",
                }],
                assertions=[{
                    "type": assertion_type,
                    "target": stmt.get("element_ref", ""),
                    "desc": f"验证{stmt['description']}正常响应",
                }],
                coverage_dimension=f"语句覆盖-{stmt['id']}",
                tags=["statement_coverage", "element", "白盒"],
            ))
        return cases

    def _pick_action(self, tag: str, available_actions: list[str]) -> str:
        """根据元素标签选择合适的操作"""
        action_map = {
            "button": ["click", "focus"],
            "a": ["click", "navigate"],
            "input": ["fill", "click", "type_text", "clear"],
            "select": ["select_option", "click"],
            "textarea": ["fill", "type_text", "clear"],
            "iframe": ["switch_to_iframe"],
            "checkbox": ["check", "click"],
            "radio": ["check", "click"],
            "upload": ["upload_file", "click"],
            "date": ["set_date", "click"],
            "code": ["click", "fill"],
            "form": ["fill", "click"],
            "table": ["scroll", "click"],
            "image": ["click"],
            "navigation": ["click"],
            "list": ["scroll", "click"],
            "list_item": ["click"],
            "cell": ["click"],
            "row": ["click"],
            "text": ["click"],
            "container": ["click"],
        }
        preferred = action_map.get(tag, ["click"])
        for action in preferred:
            if action in available_actions:
                return action
        # 回退
        if "click" in available_actions:
            return "click"
        return available_actions[0] if available_actions else "click"

    def _pick_assertion(self, available_assertions: list[str]) -> str:
        """选择合适的断言类型"""
        priority = ["element_exists", "text_contains", "no_error", "element_visible"]
        for a in priority:
            if a in available_assertions:
                return a
        return available_assertions[0] if available_assertions else "element_exists"

    def _build_assertions(self, statements: list[dict],
                           available_assertions: list[str]) -> list[dict]:
        """为语句组构建断言列表"""
        assertion_list = []
        for s in statements:
            a_type = self._pick_assertion(available_assertions)
            assertion_list.append({
                "type": a_type,
                "target": s.get("element_ref", ""),
                "desc": f"验证{s['description']}存在且正常",
            })
        return assertion_list


# ── 独立运行测试 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    from .taxonomy_filter import FilteredTaxonomy

    # 模拟需求数据
    requirement = {
        "title": "用户登录页面",
        "description": "包含用户名输入框、密码输入框、登录按钮、忘记密码链接、注册链接",
        "page_structure": {
            "elements": [
                {"tag": "input", "type": "text", "id": "username", "text": "用户名"},
                {"tag": "input", "type": "password", "id": "password", "text": "密码"},
                {"tag": "button", "id": "btn-login", "text": "登录"},
                {"tag": "a", "id": "link-forgot", "text": "忘记密码"},
                {"tag": "a", "id": "link-register", "text": "注册"},
                {"tag": "input", "type": "checkbox", "id": "remember", "text": "记住我"},
            ],
        },
    }

    # 模拟 filtered_taxonomy
    ft = FilteredTaxonomy(
        actions=["navigate", "click", "fill", "type_text", "check", "clear"],
        assertions=["text_contains", "element_exists", "url_matches", "no_error", "element_visible"],
    )

    method = StatementCoverage()
    print(f"=== {method.method_name} ===")
    print(f"Category: {method.method_category.value}")
    print(f"Description: {method.description}")

    analysis = method.analyze(requirement)
    print(f"\nAnalysis: {analysis['total_statements']} statements, source={analysis['source']}")
    print(f"Categories: {analysis['categories']}")

    cases = method.generate_cases(analysis, ft)
    print(f"\nGenerated {len(cases)} test cases:")
    for c in cases:
        print(f"  [{c.case_id}] {c.name} | steps={len(c.steps)} | assertions={len(c.assertions)}")

    result = method.run(requirement, ft)
    print(f"\nResult: {result.total_cases} cases, coverage={result.coverage_report}")