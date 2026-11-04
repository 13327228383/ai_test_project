# -*- coding: utf-8 -*-
"""等价类划分法 — 黑盒测试方法

将输入域划分为有效等价类和无效等价类，每个等价类选取代表值设计测试用例。
"""

from typing import Optional
from .base import BaseMethod, TestCase, MethodCategory


class EquivalencePartitioning(BaseMethod):
    method_name: str = "equivalence_partitioning"
    method_category: MethodCategory = MethodCategory.BLACK_BOX
    description: str = "将输入域划分为有效等价类和无效等价类，每个等价类选取代表值设计测试用例"
    applicable_scenarios: list[str] = ["表单输入", "数据校验", "输入框", "字段验证"]

    def analyze(self, requirement: dict, context: Optional[dict] = None) -> dict:
        """分析需求中的 fields 字段，对每个字段划分有效等价类和无效等价类。

        Args:
            requirement: {title, description, fields: [{name, type, required, constraints, ...}], ...}
            context: 额外上下文

        Returns:
            {
                "field_partitions": {
                    field_name: {
                        "valid": [{"id": "V1", "desc": "...", "representative": ...}],
                        "invalid": [{"id": "I1", "desc": "...", "representative": ...}],
                    }
                },
                "total_valid": int,
                "total_invalid": int,
                "summary": "..."
            }
        """
        fields = requirement.get("fields", [])
        field_partitions = {}
        total_valid = 0
        total_invalid = 0

        for field in fields:
            field_name = field.get("name", "unknown")
            field_type = field.get("type", "text").lower()
            required = field.get("required", False)
            constraints = field.get("constraints", {})

            valid = []
            invalid = []

            if field_type in ("text", "string", "textarea"):
                max_len = constraints.get("max_length", 255)
                min_len = constraints.get("min_length", 0)
                pattern = constraints.get("pattern", None)

                valid.append({"id": "V1", "desc": "正常长度文本", "representative": "测试文本"})
                valid.append({"id": "V2", "desc": f"边界长度 {max_len} 字符", "representative": "A" * max_len})

                if required:
                    invalid.append({"id": "I1", "desc": "空字符串", "representative": ""})
                invalid.append({"id": "I2", "desc": f"超长文本 {max_len + 1} 字符", "representative": "A" * (max_len + 1)})
                if min_len > 0:
                    invalid.append({"id": "I3", "desc": f"长度不足 {min_len - 1} 字符", "representative": "A" * (min_len - 1) if min_len > 1 else ""})
                if pattern:
                    invalid.append({"id": "I4", "desc": "不符合正则格式", "representative": "####"})

            elif field_type in ("number", "int", "integer", "float", "decimal"):
                min_val = constraints.get("min", None)
                max_val = constraints.get("max", None)

                if min_val is not None and max_val is not None:
                    valid.append({"id": "V1", "desc": f"范围中间值 {int((min_val + max_val) / 2)}", "representative": int((min_val + max_val) / 2)})
                    valid.append({"id": "V2", "desc": f"最小值 {min_val}", "representative": min_val})
                    valid.append({"id": "V3", "desc": f"最大值 {max_val}", "representative": max_val})
                    invalid.append({"id": "I1", "desc": f"小于最小值 {min_val - 1}", "representative": min_val - 1})
                    invalid.append({"id": "I2", "desc": f"大于最大值 {max_val + 1}", "representative": max_val + 1})
                elif min_val is not None:
                    valid.append({"id": "V1", "desc": f"等于最小值 {min_val}", "representative": min_val})
                    valid.append({"id": "V2", "desc": f"大于最小值 {min_val + 10}", "representative": min_val + 10})
                    invalid.append({"id": "I1", "desc": f"小于最小值 {min_val - 1}", "representative": min_val - 1})
                elif max_val is not None:
                    valid.append({"id": "V1", "desc": f"等于最大值 {max_val}", "representative": max_val})
                    valid.append({"id": "V2", "desc": f"小于最大值 {max_val - 10}", "representative": max_val - 10})
                    invalid.append({"id": "I1", "desc": f"大于最大值 {max_val + 1}", "representative": max_val + 1})
                else:
                    valid.append({"id": "V1", "desc": "正整数", "representative": 100})
                    valid.append({"id": "V2", "desc": "零", "representative": 0})
                    invalid.append({"id": "I1", "desc": "负数", "representative": -1})

                invalid.append({"id": "I3", "desc": "非数字输入", "representative": "abc"})
                if required:
                    invalid.append({"id": "I4", "desc": "空值", "representative": None})

            elif field_type in ("email", "mail"):
                valid.append({"id": "V1", "desc": "标准邮箱格式", "representative": "test@example.com"})
                valid.append({"id": "V2", "desc": "带子域名的邮箱", "representative": "user@mail.example.com"})
                invalid.append({"id": "I1", "desc": "缺少@符号", "representative": "testexample.com"})
                invalid.append({"id": "I2", "desc": "缺少域名", "representative": "test@"})
                invalid.append({"id": "I3", "desc": "含非法字符", "representative": "test@exa mple.com"})
                if required:
                    invalid.append({"id": "I4", "desc": "空值", "representative": ""})

            elif field_type in ("phone", "tel", "mobile"):
                valid.append({"id": "V1", "desc": "合法手机号", "representative": "13800138000"})
                valid.append({"id": "V2", "desc": "合法座机号", "representative": "010-12345678"})
                invalid.append({"id": "I1", "desc": "位数不足", "representative": "13800"})
                invalid.append({"id": "I2", "desc": "含字母", "representative": "1380013800a"})
                invalid.append({"id": "I3", "desc": "含特殊字符", "representative": "138-0013-8000"})

            elif field_type in ("date", "datetime", "datepicker"):
                valid.append({"id": "V1", "desc": "正常日期", "representative": "2026-01-15"})
                valid.append({"id": "V2", "desc": "闰年2月29日", "representative": "2024-02-29"})
                invalid.append({"id": "I1", "desc": "非法日期 2月30日", "representative": "2026-02-30"})
                invalid.append({"id": "I2", "desc": "非闰年2月29日", "representative": "2025-02-29"})
                invalid.append({"id": "I3", "desc": "非日期格式", "representative": "abcdef"})

            elif field_type in ("select", "dropdown", "enum"):
                options = constraints.get("options", [])
                if options:
                    valid.append({"id": "V1", "desc": f"选择有效选项: {options[0]}", "representative": options[0]})
                    if len(options) > 1:
                        valid.append({"id": "V2", "desc": f"选择有效选项: {options[-1]}", "representative": options[-1]})
                    invalid.append({"id": "I1", "desc": "不存在的选项", "representative": "不存在的选项值"})
                if required:
                    invalid.append({"id": "I2", "desc": "不选择（留空）", "representative": None})

            elif field_type in ("checkbox", "boolean"):
                valid.append({"id": "V1", "desc": "勾选", "representative": True})
                valid.append({"id": "V2", "desc": "不勾选", "representative": False})

            elif field_type in ("url",):
                valid.append({"id": "V1", "desc": "标准URL", "representative": "https://www.example.com"})
                valid.append({"id": "V2", "desc": "带路径的URL", "representative": "https://www.example.com/path/to/page"})
                invalid.append({"id": "I1", "desc": "缺少协议", "representative": "www.example.com"})
                invalid.append({"id": "I2", "desc": "非法字符", "representative": "https://exa mple.com"})

            else:
                valid.append({"id": "V1", "desc": "正常值", "representative": "正常输入"})
                invalid.append({"id": "I1", "desc": "空值", "representative": ""})

            field_partitions[field_name] = {
                "valid": valid,
                "invalid": invalid,
            }
            total_valid += len(valid)
            total_invalid += len(invalid)

        return {
            "field_partitions": field_partitions,
            "total_valid": total_valid,
            "total_invalid": total_invalid,
            "summary": f"共 {len(fields)} 个字段，{total_valid} 个有效等价类，{total_invalid} 个无效等价类",
        }

    def generate_cases(self, analysis: dict, filtered_taxonomy: dict) -> list[TestCase]:
        """根据等价类划分分析结果生成测试用例。

        为每个字段的每个等价类生成至少一个代表值用例。

        Args:
            analysis: self.analyze() 的返回值
            filtered_taxonomy: FilteredTaxonomy 对象，包含筛选后的 actions 和 assertions

        Returns:
            list[TestCase]
        """
        cases = []
        field_partitions = analysis.get("field_partitions", {})
        actions = filtered_taxonomy.actions if hasattr(filtered_taxonomy, 'actions') else filtered_taxonomy.get("actions", [])
        assertions = filtered_taxonomy.assertions if hasattr(filtered_taxonomy, 'assertions') else filtered_taxonomy.get("assertions", [])

        case_index = 1

        for field_name, partitions in field_partitions.items():
            valid_classes = partitions.get("valid", [])
            invalid_classes = partitions.get("invalid", [])

            # 为每个有效等价类生成一个正向用例
            for vc in valid_classes:
                case_id = f"EP-{case_index:03d}"
                case_index += 1

                fill_action = "fill" if "fill" in actions else (actions[0] if actions else "fill")
                assertion_type = "text_contains" if "text_contains" in assertions else (assertions[0] if assertions else "text_contains")

                steps = [{
                    "action": fill_action,
                    "target": field_name,
                    "value": vc["representative"],
                    "desc": f"输入有效等价类代表值: {vc['desc']}",
                }]

                assertions_list = [{
                    "type": assertion_type,
                    "target": field_name,
                    "desc": f"验证有效等价类: {vc['desc']}",
                }]

                if "no_error" in assertions:
                    assertions_list.append({
                        "type": "no_error",
                        "target": "form",
                        "desc": "无错误提示",
                    })

                if "element_exists" in assertions:
                    assertions_list.append({
                        "type": "element_exists",
                        "target": "success_message",
                        "desc": "成功提示或跳转",
                    })

                cases.append(TestCase(
                    case_id=case_id,
                    name=f"有效等价类-{field_name}-{vc['desc']}",
                    method=self.method_name,
                    category=self.method_category.value,
                    priority="high",
                    module=field_name,
                    precondition=f"打开包含 {field_name} 字段的页面",
                    test_data={"field": field_name, "value": vc["representative"], "class_type": "valid", "class_id": vc["id"]},
                    steps=steps,
                    assertions=assertions_list,
                    coverage_dimension=f"有效等价类: {field_name}",
                    tags=["等价类划分", "有效等价类", field_name],
                ))

            # 为每个无效等价类生成一个负向用例
            for ic in invalid_classes:
                case_id = f"EP-{case_index:03d}"
                case_index += 1

                fill_action = "fill" if "fill" in actions else (actions[0] if actions else "fill")
                clear_action = "clear" if "clear" in actions else fill_action
                click_action = "click" if "click" in actions else fill_action

                steps = [{
                    "action": fill_action,
                    "target": field_name,
                    "value": ic["representative"],
                    "desc": f"输入无效等价类代表值: {ic['desc']}",
                }]

                # 如果是空值，可以尝试清空字段
                if ic["representative"] in (None, ""):
                    steps = [{
                        "action": clear_action,
                        "target": field_name,
                        "value": "",
                        "desc": f"清空字段: {ic['desc']}",
                    }, {
                        "action": click_action,
                        "target": "submit_button",
                        "value": None,
                        "desc": "点击提交",
                    }]

                assertions_list = [{
                    "type": "text_contains" if "text_contains" in assertions else (assertions[0] if assertions else "text_contains"),
                    "target": field_name,
                    "desc": f"验证无效等价类出现错误提示: {ic['desc']}",
                }]

                if "text_not_contains" in assertions:
                    assertions_list.append({
                        "type": "text_not_contains",
                        "target": "success_message",
                        "desc": "不应出现成功提示",
                    })

                if "element_exists" in assertions:
                    assertions_list.append({
                        "type": "element_exists",
                        "target": "error_message",
                        "desc": "出现错误提示元素",
                    })

                cases.append(TestCase(
                    case_id=case_id,
                    name=f"无效等价类-{field_name}-{ic['desc']}",
                    method=self.method_name,
                    category=self.method_category.value,
                    priority="critical",
                    module=field_name,
                    precondition=f"打开包含 {field_name} 字段的页面",
                    test_data={"field": field_name, "value": ic["representative"], "class_type": "invalid", "class_id": ic["id"]},
                    steps=steps,
                    assertions=assertions_list,
                    coverage_dimension=f"无效等价类: {field_name}",
                    tags=["等价类划分", "无效等价类", field_name],
                ))

        return cases


# ── 独立运行测试 ──
if __name__ == "__main__":
    from .taxonomy_filter import FilteredTaxonomy

    requirement = {
        "title": "用户注册表单",
        "description": "用户填写注册信息并提交",
        "fields": [
            {"name": "username", "type": "text", "required": True, "constraints": {"min_length": 3, "max_length": 20}},
            {"name": "age", "type": "number", "required": True, "constraints": {"min": 18, "max": 120}},
            {"name": "email", "type": "email", "required": True, "constraints": {}},
            {"name": "gender", "type": "select", "required": False, "constraints": {"options": ["男", "女", "其他"]}},
        ],
    }

    ft = FilteredTaxonomy(actions=["fill", "click", "clear", "select_option"], assertions=["text_contains", "text_not_contains", "element_exists", "no_error"])

    ep = EquivalencePartitioning()
    print(f"方法: {ep.method_name}, 分类: {ep.method_category.value}")
    print(f"描述: {ep.description}")

    result = ep.analyze(requirement)
    print(f"分析摘要: {result['summary']}")
    for fname, p in result["field_partitions"].items():
        print(f"  字段 {fname}: 有效={len(p['valid'])} 无效={len(p['invalid'])}")

    cases = ep.generate_cases(result, ft)
    for c in cases:
        print(f"  {c.case_id} | {c.name} | {c.priority} | steps={len(c.steps)} assertions={len(c.assertions)}")
    print(f"总共生成 {len(cases)} 个用例")