# -*- coding: utf-8 -*-
"""正交实验法 — 利用正交表从全组合中选取代表性测试点，以最少用例覆盖最多的参数组合"""

from typing import Optional
from .base import BaseMethod, TestCase, MethodCategory


# ── 常用正交表 ──────────────────────────────────────────────
# L4(2^3): 3 因素 2 水平
L4_2_3 = [
    [1, 1, 1],
    [1, 2, 2],
    [2, 1, 2],
    [2, 2, 1],
]

# L8(2^7): 7 因素 2 水平
L8_2_7 = [
    [1, 1, 1, 1, 1, 1, 1],
    [1, 1, 1, 2, 2, 2, 2],
    [1, 2, 2, 1, 1, 2, 2],
    [1, 2, 2, 2, 2, 1, 1],
    [2, 1, 2, 1, 2, 1, 2],
    [2, 1, 2, 2, 1, 2, 1],
    [2, 2, 1, 1, 2, 2, 1],
    [2, 2, 1, 2, 1, 1, 2],
]

# L9(3^4): 4 因素 3 水平
L9_3_4 = [
    [1, 1, 1, 1],
    [1, 2, 2, 2],
    [1, 3, 3, 3],
    [2, 1, 2, 3],
    [2, 2, 3, 1],
    [2, 3, 1, 2],
    [3, 1, 3, 2],
    [3, 2, 1, 3],
    [3, 3, 2, 1],
]

# L16(4^5): 5 因素 4 水平
L16_4_5 = [
    [1, 1, 1, 1, 1], [1, 2, 2, 2, 2], [1, 3, 3, 3, 3], [1, 4, 4, 4, 4],
    [2, 1, 2, 3, 4], [2, 2, 1, 4, 3], [2, 3, 4, 1, 2], [2, 4, 3, 2, 1],
    [3, 1, 3, 4, 2], [3, 2, 4, 3, 1], [3, 3, 1, 2, 4], [3, 4, 2, 1, 3],
    [4, 1, 4, 2, 3], [4, 2, 3, 1, 4], [4, 3, 2, 4, 1], [4, 4, 1, 3, 2],
]

# L25(5^6): 6 因素 5 水平
L25_5_6 = [
    [1, 1, 1, 1, 1, 1], [1, 2, 2, 2, 2, 2], [1, 3, 3, 3, 3, 3], [1, 4, 4, 4, 4, 4], [1, 5, 5, 5, 5, 5],
    [2, 1, 2, 3, 4, 5], [2, 2, 3, 4, 5, 1], [2, 3, 4, 5, 1, 2], [2, 4, 5, 1, 2, 3], [2, 5, 1, 2, 3, 4],
    [3, 1, 3, 5, 2, 4], [3, 2, 4, 1, 3, 5], [3, 3, 5, 2, 4, 1], [3, 4, 1, 3, 5, 2], [3, 5, 2, 4, 1, 3],
    [4, 1, 4, 2, 5, 3], [4, 2, 5, 3, 1, 4], [4, 3, 1, 4, 2, 5], [4, 4, 2, 5, 3, 1], [4, 5, 3, 1, 4, 2],
    [5, 1, 5, 4, 3, 2], [5, 2, 1, 5, 4, 3], [5, 3, 2, 1, 5, 4], [5, 4, 3, 2, 1, 5], [5, 5, 4, 3, 2, 1],
]

# 正交表索引：(max_levels, min_factors) -> table
ORTHOGONAL_TABLES = [
    (2,  3,  L4_2_3,   "L4(2^3)"),
    (2,  7,  L8_2_7,   "L8(2^7)"),
    (3,  4,  L9_3_4,   "L9(3^4)"),
    (4,  5,  L16_4_5,  "L16(4^5)"),
    (5,  6,  L25_5_6,  "L25(5^6)"),
]


class OrthogonalArray(BaseMethod):
    """正交实验法

    利用正交表从全组合中选取代表性测试点，
    以最少用例覆盖最多的参数组合。
    """

    method_name: str = "orthogonal_array"
    method_category: MethodCategory = MethodCategory.BLACK_BOX
    description: str = "利用正交表从全组合中选取代表性测试点，以最少用例覆盖最多的参数组合"
    applicable_scenarios: list[str] = ["多参数多取值", "配置项组合", "搜索条件组合", "兼容性测试"]

    # ── analyze ──────────────────────────────────────────────
    def analyze(self, requirement: dict, context: Optional[dict] = None) -> dict:
        """分析需求中的参数及其取值，选出匹配的正交表

        Args:
            requirement: {title, description, fields: [{name, type, values: [...]}], ...}

        Returns:
            {
                parameters: [{name, values: [...]}],
                param_count, max_levels,
                selected_table: {name, table, rows, cols},
                total_combinations,
            }
        """
        fields = requirement.get("fields", [])
        parameters = []

        for f in fields:
            name = f.get("name", f.get("field", ""))
            vals = f.get("values", [])
            if not name:
                continue
            # 如果字段没有显式 values，但有 type 和 range，则推导取值
            if not vals:
                vals = self._derive_values(f)
            if vals:
                parameters.append({"name": name, "values": vals, "type": f.get("type", "text")})

        if not parameters:
            return {"parameters": [], "param_count": 0, "max_levels": 0,
                    "selected_table": None, "total_combinations": 0, "warning": "未找到可参数化的字段"}

        param_count = len(parameters)
        max_levels = max(len(p["values"]) for p in parameters)

        # 选择正交表
        selected_table, table_name, table_data = self._select_table(param_count, max_levels, parameters)

        total_combinations = 1
        for p in parameters:
            total_combinations *= len(p["values"])

        return {
            "parameters": parameters,
            "param_count": param_count,
            "max_levels": max_levels,
            "selected_table": {
                "name": table_name,
                "table": table_data,
                "rows": len(table_data),
                "cols": min(len(table_data[0]) if table_data else 0, param_count),
            },
            "total_combinations": total_combinations,
            "reduction_ratio": f"{len(table_data) / total_combinations * 100:.1f}%" if total_combinations > 0 else "N/A",
        }

    def _derive_values(self, field: dict) -> list:
        """根据字段类型和约束推导取值"""
        ftype = field.get("type", "").lower()
        values = []

        if ftype in ("text", "string", "varchar"):
            values = ["有效字符串", "空字符串", "超长字符串"]
        elif ftype in ("number", "int", "integer", "float", "decimal"):
            mn = field.get("min", field.get("min_value", 0))
            mx = field.get("max", field.get("max_value", 100))
            values = [str(mn), str(mx), str((mx + mn) // 2)]
        elif ftype in ("boolean", "bool"):
            values = ["true", "false"]
        elif ftype in ("select", "enum", "dropdown"):
            options = field.get("options", field.get("enum", []))
            values = options if options else ["选项A", "选项B", "选项C"]
        elif ftype in ("date", "datetime"):
            values = ["有效日期", "无效日期", "边界日期"]
        elif ftype in ("email",):
            values = ["有效邮箱", "无效邮箱", "空值"]
        elif ftype in ("file", "upload"):
            values = ["有效文件", "超限文件", "错误类型文件"]
        else:
            values = ["正常值", "异常值", "边界值"]

        return values

    def _select_table(self, param_count: int, max_levels: int, parameters: list) -> tuple:
        """选择匹配的正交表

        返回 (table_data, table_name, table_data)
        """
        best = None
        best_name = ""
        best_data = []

        for levels, factors, table, name in ORTHOGONAL_TABLES:
            if levels >= max_levels and factors >= param_count:
                best = table
                best_name = name
                best_data = table
                break  # 取第一个满足条件的

        if best is None:
            # 没有完美匹配，使用最大的正交表并截断
            _, _, best_data, best_name = ORTHOGONAL_TABLES[-1]
            best = best_data

        return best, best_name, best_data

    # ── generate_cases ───────────────────────────────────────
    def generate_cases(self, analysis: dict, filtered_taxonomy) -> list[TestCase]:
        """根据正交表生成测试用例，每行对应一个用例

        Args:
            analysis: analyze() 的返回值
            filtered_taxonomy: FilteredTaxonomy 实例，含 .actions 和 .assertions

        Returns:
            list[TestCase]
        """
        parameters = analysis.get("parameters", [])
        # analyze() 在无参数时返回 selected_table=None，此处需容错
        table_info = analysis.get("selected_table") or {}
        table = (table_info or {}).get("table", []) or []
        table_name = table_info.get("name", "N/A")

        if not parameters or not table:
            return []

        actions = getattr(filtered_taxonomy, "actions", []) or []
        assertions = getattr(filtered_taxonomy, "assertions", []) or []

        # 默认操作和断言
        if not actions:
            actions = ["click", "fill", "select_option"]
        if not assertions:
            assertions = ["text_contains", "element_exists", "no_error"]

        cases = []
        param_count = len(parameters)

        for row_idx, row in enumerate(table):
            # 只取前 param_count 列
            selected_cols = row[:param_count]

            case_id = f"OA-{row_idx + 1:03d}"
            name_parts = []
            steps = []
            test_data = {}

            for col_idx, level in enumerate(selected_cols):
                if col_idx >= len(parameters):
                    break
                param = parameters[col_idx]
                pname = param["name"]
                pvals = param["values"]
                ptype = param.get("type", "text")

                # level 是 1-based，映射到实际值
                level_idx = min(level - 1, len(pvals) - 1)
                actual_value = pvals[level_idx]

                name_parts.append(f"{pname}={actual_value}")
                test_data[pname] = actual_value

                # 生成步骤：挑选合适的 action
                action = self._pick_action(ptype, actions)
                target = f"#{pname}"
                step = {
                    "action": action,
                    "target": target,
                    "value": str(actual_value),
                    "desc": f"设置 {pname} 为 {actual_value}",
                }
                steps.append(step)

            # 最后添加一个触发动作（如点击提交按钮）
            if "click" in actions:
                steps.append({
                    "action": "click",
                    "target": "#submit-btn",
                    "value": "",
                    "desc": "点击提交按钮触发操作",
                })

            # 生成断言
            case_assertions = []
            for atype in assertions:
                case_assertions.append({
                    "type": atype,
                    "target": "#result",
                    "desc": f"验证操作结果符合预期（{atype}）",
                })

            case_name = "正交用例-" + "; ".join(name_parts)

            cases.append(TestCase(
                case_id=case_id,
                name=case_name,
                method=self.method_name,
                category=self.method_category.value,
                priority="medium",
                module="",
                precondition=f"基于正交表 {table_name} 行 {row_idx + 1}",
                test_data=test_data,
                steps=steps,
                assertions=case_assertions,
                coverage_dimension=f"正交表 {table_name} 覆盖 {param_count} 参数",
                tags=["orthogonal_array", f"row_{row_idx + 1}"],
            ))

        return cases

    def _pick_action(self, field_type: str, available_actions: list[str]) -> str:
        """根据字段类型从可用操作中挑选合适的动作"""
        ftype = field_type.lower()
        if ftype in ("select", "enum", "dropdown"):
            if "select_option" in available_actions:
                return "select_option"
        elif ftype in ("checkbox", "boolean", "bool"):
            if "check" in available_actions:
                return "check"
        elif ftype in ("file", "upload"):
            if "upload_file" in available_actions:
                return "upload_file"
        elif ftype in ("date", "datetime"):
            if "set_date" in available_actions:
                return "set_date"
        if "fill" in available_actions:
            return "fill"
        if "click" in available_actions:
            return "click"
        return available_actions[0] if available_actions else "fill"