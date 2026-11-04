# -*- coding: utf-8 -*-
"""错误推测法 — 基于测试经验和常见缺陷模式，推测可能出错的场景并设计测试用例"""

from typing import Optional
from .base import BaseMethod, TestCase, MethodCategory


# ── 常见错误模式库 ──────────────────────────────────────────
ERROR_PATTERNS = {
    # 输入类错误
    "sql_injection": {
        "category": "安全",
        "description": "SQL注入攻击",
        "payload": "' OR '1'='1",
        "field_types": ["text", "string", "varchar", "email", "password", "search"],
        "priority": "critical",
    },
    "xss": {
        "category": "安全",
        "description": "跨站脚本攻击(XSS)",
        "payload": "<script>alert('xss')</script>",
        "field_types": ["text", "string", "varchar", "textarea", "richtext"],
        "priority": "critical",
    },
    "command_injection": {
        "category": "安全",
        "description": "命令注入",
        "payload": "; rm -rf /",
        "field_types": ["text", "string", "varchar"],
        "priority": "critical",
    },
    "html_injection": {
        "category": "安全",
        "description": "HTML标签注入",
        "payload": "<h1>Injected</h1>",
        "field_types": ["text", "string", "richtext", "textarea"],
        "priority": "high",
    },
    "empty_value": {
        "category": "边界",
        "description": "空值输入",
        "payload": "",
        "field_types": ["text", "string", "number", "email", "password", "date", "varchar", "int", "float"],
        "priority": "high",
    },
    "only_spaces": {
        "category": "边界",
        "description": "仅空格/空白字符",
        "payload": "     ",
        "field_types": ["text", "string", "varchar", "textarea"],
        "priority": "medium",
    },
    "max_length_plus_one": {
        "category": "边界",
        "description": "超长输入（max_length + 1）",
        "payload": "",  # 动态生成
        "field_types": ["text", "string", "varchar", "textarea", "password"],
        "priority": "high",
    },
    "special_characters": {
        "category": "格式",
        "description": "特殊字符输入",
        "payload": "!@#$%^&*()_+-=[]{}|;':\",./<>?",
        "field_types": ["text", "string", "varchar", "textarea", "password"],
        "priority": "medium",
    },
    "unicode_emoji": {
        "category": "格式",
        "description": "Unicode/Emoji字符",
        "payload": "测试🎉💯中文emoji\u2764\ufe0f",
        "field_types": ["text", "string", "varchar", "textarea"],
        "priority": "low",
    },
    "numeric_overflow": {
        "category": "数值",
        "description": "数值溢出",
        "payload": "999999999999999999999999999999",
        "field_types": ["number", "int", "integer", "float", "decimal"],
        "priority": "high",
    },
    "negative_number": {
        "category": "数值",
        "description": "负数输入",
        "payload": "-1",
        "field_types": ["number", "int", "integer", "float", "decimal"],
        "priority": "medium",
    },
    "zero_value": {
        "category": "数值",
        "description": "零值输入",
        "payload": "0",
        "field_types": ["number", "int", "integer", "float", "decimal"],
        "priority": "medium",
    },
    "decimal_precision": {
        "category": "数值",
        "description": "小数精度超出",
        "payload": "3.14159265358979323846",
        "field_types": ["float", "decimal"],
        "priority": "low",
    },
    "invalid_email": {
        "category": "格式",
        "description": "无效邮箱格式",
        "payload": "not-an-email",
        "field_types": ["email"],
        "priority": "high",
    },
    "invalid_date": {
        "category": "格式",
        "description": "无效日期",
        "payload": "2025-02-30",
        "field_types": ["date", "datetime"],
        "priority": "high",
    },
    "future_date": {
        "category": "业务",
        "description": "未来日期（超出业务范围）",
        "payload": "2099-12-31",
        "field_types": ["date", "datetime"],
        "priority": "low",
    },
    "past_date": {
        "category": "业务",
        "description": "过于久远的日期",
        "payload": "1900-01-01",
        "field_types": ["date", "datetime"],
        "priority": "low",
    },
    "file_type_mismatch": {
        "category": "文件",
        "description": "文件类型不匹配",
        "payload": "malicious.exe",  # 文件名
        "field_types": ["file", "upload"],
        "priority": "high",
    },
    "file_size_exceeded": {
        "category": "文件",
        "description": "文件大小超限",
        "payload": "oversized_file.bin",
        "field_types": ["file", "upload"],
        "priority": "high",
    },
    "concurrent_submission": {
        "category": "并发",
        "description": "重复/并发提交",
        "payload": "__CONCURRENT__",
        "field_types": ["text", "string", "number", "email", "form", "all"],
        "priority": "high",
    },
    "null_value": {
        "category": "边界",
        "description": "null值",
        "payload": None,
        "field_types": ["text", "string", "number", "date", "email", "all"],
        "priority": "high",
    },
    "leading_trailing_spaces": {
        "category": "格式",
        "description": "首尾空格",
        "payload": "  value_with_spaces  ",
        "field_types": ["text", "string", "varchar", "email"],
        "priority": "low",
    },
    "only_numbers_in_text": {
        "category": "格式",
        "description": "纯数字输入文本字段",
        "payload": "1234567890",
        "field_types": ["text", "string", "varchar"],
        "priority": "low",
    },
    "very_long_string": {
        "category": "边界",
        "description": "超长字符串（10000+字符）",
        "payload": "",  # 动态生成
        "field_types": ["text", "string", "varchar", "textarea"],
        "priority": "medium",
    },
}


class ErrorGuessing(BaseMethod):
    """错误推测法

    基于常见错误模式库（SQL注入、XSS、空值、超长、特殊字符、并发等），
    分析需求中的字段，对每个字段 × 每个适用错误模式生成测试用例。
    """

    method_name: str = "error_guessing"
    method_category: MethodCategory = MethodCategory.BLACK_BOX
    description: str = "基于测试经验和常见缺陷模式，推测可能出错的场景并设计测试用例"
    applicable_scenarios: list[str] = ["表单输入", "安全测试", "健壮性测试", "边界测试", "异常处理"]

    # ── analyze ──────────────────────────────────────────────
    def analyze(self, requirement: dict, context: Optional[dict] = None) -> dict:
        """分析需求中的字段，匹配适用的错误推测模式

        Args:
            requirement: {
                title, description,
                fields: [{name, type, max_length, min, max, required, validations}, ...],
            }

        Returns:
            {
                fields: [{name, type, applicable_patterns: [pattern_name, ...]}],
                total_patterns,
                total_field_pattern_pairs,
            }
        """
        fields = requirement.get("fields", [])
        if not fields:
            # 尝试从 description 推测字段
            fields = self._infer_fields(requirement)

        field_analysis = []
        total_pairs = 0

        for f in fields:
            fname = f.get("name", f.get("field", f.get("label", "")))
            ftype = f.get("type", "text").lower()
            f_required = f.get("required", False)
            f_max_length = f.get("max_length", f.get("maxlength", f.get("max", 0)))

            if not fname:
                continue

            applicable = []
            for pattern_name, pattern_info in ERROR_PATTERNS.items():
                applicable_types = pattern_info.get("field_types", [])
                if "all" in applicable_types or ftype in applicable_types:
                    applicable.append({
                        "pattern_name": pattern_name,
                        "category": pattern_info["category"],
                        "description": pattern_info["description"],
                        "payload": pattern_info["payload"],
                        "priority": pattern_info["priority"],
                    })

            # 特殊处理：max_length_plus_one 需要动态 payload
            for p in applicable:
                if p["pattern_name"] == "max_length_plus_one" and f_max_length:
                    p["payload"] = "X" * (int(f_max_length) + 1)
                elif p["pattern_name"] == "very_long_string":
                    p["payload"] = "X" * 10000

            field_analysis.append({
                "name": fname,
                "type": ftype,
                "required": f_required,
                "max_length": f_max_length,
                "applicable_patterns": applicable,
            })
            total_pairs += len(applicable)

        return {
            "fields": field_analysis,
            "total_patterns": len(ERROR_PATTERNS),
            "total_field_pattern_pairs": total_pairs,
        }

    def _infer_fields(self, requirement: dict) -> list:
        """从 description 中推测字段"""
        description = requirement.get("description", "")
        if not description:
            return []

        import re
        fields = []
        # 匹配常见的字段名模式
        field_patterns = [
            r'["\u201c]([^"\u201d]+)["\u201d]\s*(?:字段|输入|框)',
            r'([\u4e00-\u9fa5a-zA-Z_]+)\s*(?:字段|输入框|输入)',
        ]
        found = set()
        for pattern in field_patterns:
            for match in re.finditer(pattern, description):
                name = match.group(1).strip()
                if name and name not in found and len(name) < 20:
                    found.add(name)

        for name in found:
            fields.append({"name": name, "type": "text"})

        return fields

    # ── generate_cases ───────────────────────────────────────
    def generate_cases(self, analysis: dict, filtered_taxonomy) -> list[TestCase]:
        """为每个字段 × 每个适用错误模式生成测试用例

        Args:
            analysis: analyze() 的返回值
            filtered_taxonomy: FilteredTaxonomy 实例

        Returns:
            list[TestCase]
        """
        field_analyses = analysis.get("fields", [])

        actions = getattr(filtered_taxonomy, "actions", []) or []
        assertions = getattr(filtered_taxonomy, "assertions", []) or []

        if not actions:
            actions = ["fill", "click", "clear", "type_text", "upload_file"]
        if not assertions:
            assertions = ["text_contains", "text_not_contains", "toast", "no_error", "console_error", "element_enabled"]

        cases = []
        seq = 0

        for field_info in field_analyses:
            fname = field_info["name"]
            ftype = field_info.get("type", "text")
            f_required = field_info.get("required", False)

            for pattern in field_info.get("applicable_patterns", []):
                seq += 1
                pattern_name = pattern["pattern_name"]
                category = pattern["category"]
                desc = pattern["description"]
                payload = pattern["payload"]
                priority = pattern["priority"]

                case_id = f"EG-{seq:03d}"

                # 构建步骤
                steps = self._build_error_steps(fname, ftype, payload, pattern_name, actions)

                # 构建断言
                case_assertions = self._build_error_assertions(pattern_name, assertions)

                case_name = f"[{category}] {fname}: {desc}"

                # 并发提交通常设计整个表单，而非单个字段
                tags = ["error_guessing", category, pattern_name, fname]

                if pattern_name == "concurrent_submission":
                    precondition = "系统已加载表单，准备好提交"
                    tags.append("concurrent")
                elif pattern_name == "null_value":
                    precondition = f"字段 {fname} 为必填={f_required}"
                elif pattern_name == "empty_value":
                    precondition = f"字段 {fname} 为必填={f_required}，准备输入空值"
                else:
                    precondition = f"字段 {fname} 已定位，准备输入测试数据"

                cases.append(TestCase(
                    case_id=case_id,
                    name=case_name,
                    method=self.method_name,
                    category=self.method_category.value,
                    priority=priority,
                    module=fname,
                    precondition=precondition,
                    test_data={"field": fname, "field_type": ftype, "pattern": pattern_name,
                               "payload": str(payload) if payload is not None else "null"},
                    steps=steps,
                    assertions=case_assertions,
                    coverage_dimension=f"错误推测: {fname} × {pattern_name} ({category})",
                    tags=tags,
                ))

        return cases

    def _build_error_steps(self, field_name: str, field_type: str, payload,
                           pattern_name: str, actions: list[str]) -> list[dict]:
        """构建错误注入的操作步骤"""
        steps = []
        target = f"#{field_name}"

        # 根据字段类型选择操作
        if field_type in ("file", "upload"):
            action = "upload_file" if "upload_file" in actions else "click"
            steps.append({
                "action": action,
                "target": target,
                "value": str(payload) if payload else "",
                "desc": f"上传文件: {payload}（{pattern_name}）",
            })
        elif field_type in ("select", "enum", "dropdown"):
            steps.append({
                "action": "click",
                "target": target,
                "value": "",
                "desc": f"点击下拉框 {field_name}",
            })
        elif field_type in ("checkbox", "boolean", "bool"):
            steps.append({
                "action": "check" if "check" in actions else "click",
                "target": target,
                "value": str(payload) if payload is not None else "",
                "desc": f"设置复选框 {field_name}（{pattern_name}）",
            })
        elif pattern_name in ("empty_value", "null_value"):
            steps.append({
                "action": "clear" if "clear" in actions else "fill",
                "target": target,
                "value": "",
                "desc": f"清空字段 {field_name}（{pattern_name}）",
            })
        elif pattern_name == "concurrent_submission":
            # 并发提交：先正常填写，再快速重复提交
            steps.append({
                "action": "fill" if "fill" in actions else "type_text",
                "target": target,
                "value": "normal_value",
                "desc": f"正常填写字段 {field_name}",
            })
            steps.append({
                "action": "click",
                "target": "#submit-btn",
                "value": "",
                "desc": "快速连续点击提交（模拟并发）",
            })
        else:
            action = "fill" if "fill" in actions else "type_text"
            # 对于超长字符串等，使用 type_text 更合适
            if pattern_name in ("max_length_plus_one", "very_long_string"):
                action = "type_text" if "type_text" in actions else action
            steps.append({
                "action": action,
                "target": target,
                "value": str(payload) if payload is not None else "",
                "desc": f"输入错误数据到 {field_name}: {pattern_name}",
            })

        # 非并发提交场景，添加提交步骤
        if pattern_name != "concurrent_submission":
            if "click" in actions:
                steps.append({
                    "action": "click",
                    "target": "#submit-btn",
                    "value": "",
                    "desc": "提交表单",
                })

        return steps

    def _build_error_assertions(self, pattern_name: str, assertions: list[str]) -> list[dict]:
        """根据错误模式构建断言"""
        result = []

        if pattern_name in ("sql_injection", "xss", "command_injection", "html_injection"):
            # 安全类：必须验证未执行恶意代码
            result.append({
                "type": "text_not_contains",
                "target": "#page",
                "desc": "验证页面未执行注入的恶意代码",
            })
            result.append({
                "type": "no_error",
                "target": "#console",
                "desc": "验证无JS错误或未捕获异常",
            })
        elif pattern_name in ("empty_value", "null_value"):
            result.append({
                "type": "text_contains",
                "target": "#error-msg",
                "desc": "验证显示必填字段提示",
            })
            result.append({
                "type": "element_enabled",
                "target": "#submit-btn",
                "desc": "验证提交按钮状态正确",
            })
        elif pattern_name in ("max_length_plus_one", "very_long_string"):
            result.append({
                "type": "text_contains",
                "target": "#error-msg",
                "desc": "验证显示长度限制提示",
            })
            result.append({
                "type": "no_error",
                "target": "#console",
                "desc": "验证无溢出或崩溃",
            })
        elif pattern_name == "concurrent_submission":
            result.append({
                "type": "element_count",
                "target": "#result-list",
                "desc": "验证仅创建一条记录（无重复提交）",
            })
            result.append({
                "type": "no_error",
                "target": "#console",
                "desc": "验证无并发异常",
            })
        elif pattern_name in ("file_type_mismatch", "file_size_exceeded"):
            result.append({
                "type": "text_contains",
                "target": "#error-msg",
                "desc": "验证显示文件校验错误",
            })
        elif pattern_name in ("invalid_email", "invalid_date"):
            result.append({
                "type": "text_contains",
                "target": "#error-msg",
                "desc": "验证显示格式校验错误",
            })
        elif pattern_name in ("numeric_overflow", "negative_number", "zero_value"):
            result.append({
                "type": "text_contains",
                "target": "#error-msg",
                "desc": "验证显示数值校验错误",
            })
        else:
            result.append({
                "type": "no_error",
                "target": "#page",
                "desc": "验证系统正确处理异常输入",
            })

        # 补充通用断言
        for atype in assertions:
            if not any(a["type"] == atype for a in result):
                result.append({
                    "type": atype,
                    "target": "#page",
                    "desc": f"通用验证（{atype}）",
                })

        return result