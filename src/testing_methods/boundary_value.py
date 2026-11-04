# -*- coding: utf-8 -*-
from __future__ import annotations

"""边界值分析法 — 黑盒测试方法

基于边界值六点法（min-1/min/min+1/max-1/max/max+1）设计测试用例。
"""

from typing import Optional
from .base import BaseMethod, TestCase, MethodCategory


class BoundaryValueAnalysis(BaseMethod):
    method_name: str = "boundary_value"
    method_category: MethodCategory = MethodCategory.BLACK_BOX
    description: str = "基于边界值六点法（min-1/min/min+1/max-1/max/max+1）设计测试用例"
    applicable_scenarios: list[str] = ["数值输入", "范围校验", "区间", "长度限制", "数量"]

    def analyze(self, requirement: dict, context: Optional[dict] = None) -> dict:
        """分析需求中的数值/范围/长度字段，提取边界信息。

        Args:
            requirement: {title, description, fields: [{name, type, constraints: {min, max, min_length, max_length}}], ...}
            context: 额外上下文

        Returns:
            {
                "boundaries": {
                    field_name: {
                        "type": "number" | "length",
                        "min": int | None,
                        "max": int | None,
                        "boundary_points": [{label, value, expected, desc}],
                    }
                },
                "total_boundary_fields": int,
                "total_boundary_points": int,
                "summary": "..."
            }
        """
        fields = requirement.get("fields", [])
        boundaries = {}
        total_boundary_points = 0

        for field in fields:
            field_name = field.get("name", "unknown")
            field_type = field.get("type", "text").lower()
            constraints = field.get("constraints", {})

            field_boundaries = {}

            # 数值类型边界
            if field_type in ("number", "int", "integer", "float", "decimal"):
                min_val = constraints.get("min", None)
                max_val = constraints.get("max", None)

                if min_val is not None or max_val is not None:
                    field_boundaries["type"] = "number"
                    field_boundaries["min"] = min_val
                    field_boundaries["max"] = max_val
                    boundary_points = []

                    if min_val is not None:
                        boundary_points.extend([
                            {"label": "min-1", "value": min_val - 1, "expected": "fail", "desc": f"最小值下界: {min_val - 1}"},
                            {"label": "min", "value": min_val, "expected": "pass", "desc": f"最小值边界: {min_val}"},
                            {"label": "min+1", "value": min_val + 1, "expected": "pass", "desc": f"最小值上界: {min_val + 1}"},
                        ])

                    if max_val is not None:
                        boundary_points.extend([
                            {"label": "max-1", "value": max_val - 1, "expected": "pass", "desc": f"最大值下界: {max_val - 1}"},
                            {"label": "max", "value": max_val, "expected": "pass", "desc": f"最大值边界: {max_val}"},
                            {"label": "max+1", "value": max_val + 1, "expected": "fail", "desc": f"最大值上界: {max_val + 1}"},
                        ])

                    # 如果 min 和 max 重叠（如 min=0, max=0），去重
                    seen_values = set()
                    unique_points = []
                    for bp in boundary_points:
                        if bp["value"] not in seen_values:
                            seen_values.add(bp["value"])
                            unique_points.append(bp)
                    boundary_points = unique_points

                    field_boundaries["boundary_points"] = boundary_points
                    total_boundary_points += len(boundary_points)

            # 文本长度边界
            if field_type in ("text", "string", "textarea", "password"):
                min_len = constraints.get("min_length", None)
                max_len = constraints.get("max_length", None)

                if min_len is not None or max_len is not None:
                    field_boundaries["type"] = "length"
                    field_boundaries["min_length"] = min_len
                    field_boundaries["max_length"] = max_len
                    boundary_points = []

                    if min_len is not None and min_len > 0:
                        boundary_points.extend([
                            {"label": f"len=min-1", "value": "A" * (min_len - 1), "expected": "fail", "desc": f"长度下界: {min_len - 1} 字符"},
                            {"label": f"len=min", "value": "A" * min_len, "expected": "pass", "desc": f"长度边界: {min_len} 字符"},
                            {"label": f"len=min+1", "value": "A" * (min_len + 1), "expected": "pass", "desc": f"长度上界: {min_len + 1} 字符"},
                        ])

                    if max_len is not None:
                        if max_len > 1:
                            boundary_points.extend([
                                {"label": f"len=max-1", "value": "A" * (max_len - 1), "expected": "pass", "desc": f"长度下界: {max_len - 1} 字符"},
                            ])
                        boundary_points.extend([
                            {"label": f"len=max", "value": "A" * max_len, "expected": "pass", "desc": f"长度边界: {max_len} 字符"},
                            {"label": f"len=max+1", "value": "A" * (max_len + 1), "expected": "fail", "desc": f"长度上界: {max_len + 1} 字符"},
                        ])

                    # 去重
                    seen_values = set()
                    unique_points = []
                    for bp in boundary_points:
                        if bp["value"] not in seen_values:
                            seen_values.add(bp["value"])
                            unique_points.append(bp)
                    boundary_points = unique_points

                    field_boundaries["boundary_points"] = boundary_points
                    total_boundary_points += len(boundary_points)

            if field_boundaries:
                # 额外补充: 特殊值边界
                if field_type in ("number", "int", "integer"):
                    special_points = [
                        {"label": "zero", "value": 0, "expected": "depends", "desc": "零值边界"},
                        {"label": "negative", "value": -1, "expected": "depends", "desc": "负数边界"},
                    ]
                    # 只添加不在已有边界内的特殊值
                    existing_values = {bp["value"] for bp in field_boundaries["boundary_points"]}
                    for sp in special_points:
                        if sp["value"] not in existing_values:
                            field_boundaries["boundary_points"].append(sp)
                            total_boundary_points += 1

                boundaries[field_name] = field_boundaries

        return {
            "boundaries": boundaries,
            "total_boundary_fields": len(boundaries),
            "total_boundary_points": total_boundary_points,
            "summary": f"共 {len(boundaries)} 个字段有边界值，{total_boundary_points} 个边界点",
        }

    def generate_cases(self, analysis: dict, filtered_taxonomy: dict) -> list[TestCase]:
        """根据边界值分析结果生成测试用例，每个边界点生成一个用例。

        Args:
            analysis: self.analyze() 的返回值
            filtered_taxonomy: FilteredTaxonomy 对象，包含筛选后的 actions 和 assertions

        Returns:
            list[TestCase]
        """
        cases = []
        boundaries = analysis.get("boundaries", {})
        actions = filtered_taxonomy.actions if hasattr(filtered_taxonomy, 'actions') else filtered_taxonomy.get("actions", [])
        assertions = filtered_taxonomy.assertions if hasattr(filtered_taxonomy, 'assertions') else filtered_taxonomy.get("assertions", [])

        case_index = 1

        for field_name, field_boundary in boundaries.items():
            boundary_type = field_boundary.get("type", "number")
            boundary_points = field_boundary.get("boundary_points", [])

            for bp in boundary_points:
                case_id = f"BV-{case_index:03d}"
                case_index += 1

                expected = bp["expected"]
                is_pass = expected == "pass"
                is_depends = expected == "depends"

                # 确定操作
                fill_action = "fill" if "fill" in actions else (actions[0] if actions else "fill")
                clear_action = "clear" if "clear" in actions else fill_action
                click_action = "click" if "click" in actions else fill_action

                steps = [{
                    "action": fill_action,
                    "target": field_name,
                    "value": bp["value"],
                    "desc": f"边界值输入 ({bp['label']}): {bp['desc']}",
                }]

                # 构建断言
                assertions_list = []

                if is_pass:
                    assertions_list.append({
                        "type": "text_contains" if "text_contains" in assertions else (assertions[0] if assertions else "text_contains"),
                        "target": field_name,
                        "desc": f"边界值通过校验: {bp['desc']}",
                    })
                    if "no_error" in assertions:
                        assertions_list.append({
                            "type": "no_error",
                            "target": "form",
                            "desc": "边界值通过，无错误提示",
                        })
                    if "element_enabled" in assertions:
                        assertions_list.append({
                            "type": "element_enabled",
                            "target": "submit_button",
                            "desc": "边界值通过，提交按钮可用",
                        })
                elif is_depends:
                    assertions_list.append({
                        "type": "text_contains" if "text_contains" in assertions else (assertions[0] if assertions else "text_contains"),
                        "target": field_name,
                        "desc": f"特殊边界值: {bp['desc']}",
                    })
                else:
                    assertions_list.append({
                        "type": "text_contains" if "text_contains" in assertions else (assertions[0] if assertions else "text_contains"),
                        "target": field_name,
                        "desc": f"边界值超出范围，应有错误提示: {bp['desc']}",
                    })
                    if "toast" in assertions:
                        assertions_list.append({
                            "type": "toast",
                            "target": "form",
                            "desc": f"边界值越界，出现提示: {bp['desc']}",
                        })
                    if "element_enabled" in assertions:
                        assertions_list.append({
                            "type": "element_enabled",
                            "target": "submit_button",
                            "desc": "边界值越界，提交按钮可能禁用",
                        })
                    if "text_not_contains" in assertions:
                        assertions_list.append({
                            "type": "text_not_contains",
                            "target": "success_message",
                            "desc": "边界值越界，不应出现成功提示",
                        })

                priority = "critical" if (not is_pass and not is_depends) else "high"

                cases.append(TestCase(
                    case_id=case_id,
                    name=f"边界值-{field_name}-{bp['label']}",
                    method=self.method_name,
                    category=self.method_category.value,
                    priority=priority,
                    module=field_name,
                    precondition=f"打开包含 {field_name} 字段的页面",
                    test_data={
                        "field": field_name,
                        "value": bp["value"],
                        "boundary_type": boundary_type,
                        "boundary_label": bp["label"],
                        "expected": bp["expected"],
                    },
                    steps=steps,
                    assertions=assertions_list,
                    coverage_dimension=f"边界值: {field_name} ({boundary_type})",
                    tags=["边界值分析", "六点法", bp["label"], field_name],
                ))

        return cases


# ── 独立运行测试 ──
if __name__ == "__main__":
    from .taxonomy_filter import FilteredTaxonomy

    requirement = {
        "title": "商品价格设置",
        "description": "设置商品价格，价格必须在1到99999之间",
        "fields": [
            {"name": "price", "type": "number", "required": True, "constraints": {"min": 1, "max": 99999}},
            {"name": "product_name", "type": "text", "required": True, "constraints": {"min_length": 2, "max_length": 50}},
            {"name": "quantity", "type": "number", "required": True, "constraints": {"min": 0, "max": 1000}},
        ],
    }

    ft = FilteredTaxonomy(
        actions=["fill", "click", "clear", "type_text"],
        assertions=["text_contains", "text_not_contains", "toast", "no_error", "element_enabled"],
    )

    bv = BoundaryValueAnalysis()
    print(f"方法: {bv.method_name}, 分类: {bv.method_category.value}")
    print(f"描述: {bv.description}")

    result = bv.analyze(requirement)
    print(f"分析摘要: {result['summary']}")
    for fname, b in result["boundaries"].items():
        pts = b["boundary_points"]
        print(f"  字段 {fname}: {len(pts)} 个边界点")
        for p in pts:
            print(f"    {p['label']}: value={p['value']}, expected={p['expected']}")

    cases = bv.generate_cases(result, ft)
    for c in cases:
        print(f"  {c.case_id} | {c.name} | {c.priority} | steps={len(c.steps)} assertions={len(c.assertions)}")
    print(f"总共生成 {len(cases)} 个用例")