# -*- coding: utf-8 -*-
"""
测试用例字段归一化工具函数
======================
从 ExcelHandler 类中提取的纯静态方法，处理意图动作文本清洗、标题/类型/预期结果规范化。
"""

import json, re


def _extract_fields_from_json(case_actions_json):
    """从结构化 case_actions_json 中提取 title/expected_outcome/risk_level。"""
    result = {"title": "", "expected_outcome": "", "risk_level": ""}
    if isinstance(case_actions_json, str):
        try:
            case_actions_json = json.loads(case_actions_json)
        except json.JSONDecodeError:
            return result
    if isinstance(case_actions_json, dict):
        result["title"] = str(
            case_actions_json.get("title", case_actions_json.get("intent_title", ""))
        )
        result["expected_outcome"] = str(
            case_actions_json.get(
                "expected_outcome", case_actions_json.get("expected", "")
            )
        )
        result["risk_level"] = str(
            case_actions_json.get("risk_level", case_actions_json.get("level", ""))
        )
        return result
    if isinstance(case_actions_json, list) and len(case_actions_json) > 0:
        first_step = str(case_actions_json[0]) if case_actions_json[0] else ""
        if first_step:
            result["title"] = first_step[:30]
    return result


def _normalize_title(title: str) -> str:
    """规范化测试用例标题：去前缀、防XSS/SQL注入脚本被执行、补'验证'前缀。"""
    title = str(title) if title else ""
    title = re.sub(r"^步骤\d+[：:]\s*", "", title)
    if (
        "<script>" in title.lower()
        or "<img " in title.lower()
        or "<svg" in title.lower()
    ):
        return "验证搜索框对XSS脚本注入的过滤与防护能力"
    if (
        "' or " in title.lower()
        or "union select" in title.lower()
        or "drop table" in title.lower()
    ):
        return "验证登录框对SQL注入攻击的防御能力"
    if len(title) > 10 and not title.startswith(("验证", "测试", "检查")):
        title = "验证" + title
    return title


def _normalize_case_type(raw_type: str) -> str:
    """
    类型归一化：将 AI 可能返回的各类变体统一到标准类型。
    v5：保留正向/负向/异常/边界值等测试类型原值，不强制归入「功能」。
    """
    type_map = {
        "正向": "正向",
        "负向": "负向",
        "异常": "异常",
        "功能": "功能",
        "功能测试": "功能测试",
        "ui": "UI",
        "界面": "UI",
        "UI": "UI",
        "安全": "安全",
        "安全测试": "安全",
        "易用性": "易用性",
        "兼容性": "兼容性",
        "性能": "性能",
        "边界值": "边界值",
        "边界": "边界值",
    }
    if not raw_type:
        return "功能"
    return type_map.get(str(raw_type).strip(), str(raw_type).strip())


def _extract_feature(feature, title, module):
    """根据标题关键词推断功能模块（feature），用于无 feature 字段时自动补充。"""
    if feature and str(feature).strip():
        return feature
    title_str = str(title) if title else ""
    module_str = str(module) if module else ""
    combined = title_str + " " + module_str
    keywords_map = [
        ("XSS", "安全防护-XSS注入过滤"),
        ("SQL注入", "安全防护-SQL注入防御"),
        ("SQL", "安全防护-SQL注入防御"),
        ("登录", "权限管理-用户登录"),
        ("权限", "权限管理-角色权限控制"),
        ("搜索", "搜索功能-查询与过滤"),
        ("角色管理", "角色管理-角色维护"),
        ("数据看板", "数据看板-数据展示"),
    ]
    for kw, feat in keywords_map:
        if kw.lower() in combined.lower():
            return feat
    return module_str or "未分类"


def _enforce_expected_outcome(expected, case_type, title, steps=""):
    """兜底预期结果生成器：若无预期结果，按 case_type/标题 关键词自动补充。"""
    if expected and str(expected).strip():
        return expected
    title_str = str(title) if title else ""
    case_type_str = str(case_type) if case_type else ""
    step_count = steps.count("\n") + 1 if steps else 0
    if "安全" in case_type_str or "XSS" in title_str.upper():
        result = [
            "1. 页面不弹出任何对话框或执行JavaScript代码",
            "2. 输入的特殊字符被HTML实体编码转义显示，不会被浏览器解析为HTML标签",
        ]
        if step_count >= 3:
            result.append("3. 系统返回安全提示或正常搜索结果，不暴露技术细节")
        return "\n".join(result)
    if "登录" in title_str:
        result = ["1. 登录成功跳转至系统主页"]
        if step_count >= 2:
            result.append("2. 权限范围内的功能模块正常可访问")
        if step_count >= 3:
            result.append("3. 无权限模块不可见或提示无权限")
        return "\n".join(result)
    if "边界" in title_str or "长度" in title_str:
        result = ["1. 边界值外输入被拒绝或有前端/后端校验提示"]
        if step_count >= 2:
            result.append("2. 边界值内输入正常处理")
        return "\n".join(result)
    return "1. 系统按预期正常处理请求，无报错或异常行为，页面正常加载"


def _normalize_expected_numbering(expected, steps):
    """给预期结果自动添加编号（1. 2. 3.），匹配意图动作数量。"""
    if not expected or not steps:
        return expected
    if re.search(r"^\d+\.", expected.strip()):
        return expected
    step_lines = [s for s in steps.split("\n") if s.strip()]
    step_count = len(step_lines)
    if step_count <= 1:
        return "1. %s" % expected.strip()
    parts = re.split(r"[。；;]", expected)
    parts = [x for p in parts if (x := p.strip())]
    if not parts:
        return "1. %s" % expected.strip()
    if len(parts) < step_count:
        result = [
            "%d. %s" % (i + 1, parts[0] if i == 0 else "操作正常")
            for i in range(step_count)
        ]
    else:
        result = [
            "%d. %s" % (i + 1, parts[i]) for i in range(min(len(parts), step_count))
        ]
    return "\n".join(result)


def _clean_steps(case_actions_json):
    """返回 (意图动作文本, checks_json_string)。
    从结构化动作 dict 中提取 api_checks / db_checks 后再拍平为文本，
    防止三轨校验数据在 Excel 写入时丢失。"""
    if isinstance(case_actions_json, str):
        try:
            case_actions_json = json.loads(case_actions_json)
        except (json.JSONDecodeError, TypeError, ValueError):
            case_actions_json = [case_actions_json]
    if not isinstance(case_actions_json, list):
        case_actions_json = [str(case_actions_json)]
    cleaned = []
    checks_map = {}
    for i, s in enumerate(case_actions_json):
        if isinstance(s, dict):
            api_c = s.get("api_checks", [])
            db_c = s.get("db_checks", [])
            if api_c or db_c:
                entry = {}
                if api_c:
                    entry["api_checks"] = api_c
                if db_c:
                    entry["db_checks"] = db_c
                checks_map[str(i)] = entry
            s_str = s.get("description", str(s))
        else:
            s_str = str(s) if s else ""
        s_str = re.sub(r"^步骤\d+[：:]\s*", "", s_str)
        cleaned.append(s_str)
    if not cleaned:
        return "", ""
    text = "\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(cleaned))
    checks = json.dumps(checks_map, ensure_ascii=False) if checks_map else ""
    return text, checks
