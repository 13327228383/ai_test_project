# -*- coding: utf-8 -*-
"""测试方法论确定性规则层（T01, 2026-08-06）。

背景事实（架构师 DB 实测）：
- test_cases 106 行 test_method='playwright'×65、''×41，11 种真实方法论编码产出 = 0
  （纯 prompt 摆设）→ 本模块用**确定性规则模板**兜底，让方法论真正落地到 verifications。
- _prompts.py:31 verifications action 只允许 fill|click|verify|wait；
  _build_default_test_method 兜底生成的「页面显示XX相关功能」会被
  _navigation_resolver DROP_EXPECT_KW 清洗 → 本模块不再生成模糊语义断言。

设计（主理人决策 #1，2026-08-06）：
- 规则模板**始终叠加**：AI 生成 verifications 保留在前，规则模板追加在后，
  dedup by (action, target)。
- 通用性：跨系统/行业/页面 — 模板不写死业务术语，占位符由调用方上下文解析。
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

# 方法论编码全集（★ 根因修复 2026-09-20：单一权威来源 catalog.py，18 编码，
# 与 _test_method_methods 清单页 / _test_methods.py 同源，消除口径分散）
from src.testing_methods.catalog import METHOD_CODES
from src.testing_methods.catalog import UNDECIDED_METHOD_MARKERS


# ══════════════════════════════════════════════════════════════
# 规则表：11 种真实方法论编码 → verify_templates + checks_templates
# 占位符约定（apply_method_rules 时解析）：
#   {primary_input}  主输入字段（test_method.inputs 首个字段 / operation 语义 / "输入框"）
#   {valid_value}    有效测试值（inputs 首个 value / "TEST_数据"）
#   {bv_min}         边界最小值（value_generator 或 0）
#   {bv_max}         边界最大值（999999999）
#   {primary_table}  主数据表（RAG merged_data.db_tables 首个 / module / "数据表"）
#   {op}             操作描述（explore_task.operation / ""）
#   {field_names}    字段名列表（逗号分隔，从 schema_context / data_dict 提取，如 "科目名称,科目编码,科目类型"）
# ══════════════════════════════════════════════════════════════
TEST_METHOD_RULES: dict[str, dict[str, list[dict]]] = {
    "EP": {
        "verify_templates": [
            {
                "action": "fill",
                "target": "{primary_input}",
                "value": "{valid_value}",
                "expect": "输入成功",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "有效值操作成功",
            },
            {
                "action": "fill",
                "target": "{primary_input}",
                "value": "无效值@#%$",
                "expect": "提示错误信息",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "无效值被拦截",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "element_exists", "selector": "body"},
                "expect": "页面正常加载",
            },
        ],
    },
    "BV": {
        "verify_templates": [
            {
                "action": "fill",
                "target": "{primary_input}",
                "value": "{bv_min}",
                "expect": "最小值输入成功",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "下边界值处理正确",
            },
            {
                "action": "fill",
                "target": "{primary_input}",
                "value": "{bv_max}",
                "expect": "最大值输入成功",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "上边界值处理正确",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "exact",
                "check": {"type": "element_exists", "selector": "body"},
                "expect": "存在",
            },
        ],
    },
    "CE": {
        "verify_templates": [
            {
                "action": "fill",
                "target": "{primary_input}",
                "value": "{valid_value}",
                "expect": "原因条件成立",
            },
            {
                "action": "click",
                "target": "触发按钮",
                "value": "",
                "expect": "结果符合因果图预期",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "因果组合结果正确",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "no_error", "selector": ""},
                "expect": "无错误提示",
            },
        ],
    },
    "DT": {
        "verify_templates": [
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "条件组合1结果正确",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "条件组合2结果正确",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "element_exists", "selector": "body"},
                "expect": "页面正常",
            },
        ],
    },
    "ST": {
        "verify_templates": [
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "状态A迁移到状态B成功",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "非法状态迁移被拦截",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "no_error", "selector": ""},
                "expect": "无错误提示",
            },
        ],
    },
    "OT": {
        "verify_templates": [
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "正交组合1结果正确",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "正交组合2结果正确",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "element_exists", "selector": "body"},
                "expect": "页面正常",
            },
        ],
    },
    "EG": {
        "verify_templates": [
            {
                "action": "fill",
                "target": "{primary_input}",
                "value": "",
                "expect": "提示必填",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "空值被拦截",
            },
            {
                "action": "fill",
                "target": "{primary_input}",
                "value": "特殊字符!@#",
                "expect": "特殊字符输入成功",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "no_error", "selector": ""},
                "expect": "无错误提示",
            },
        ],
    },
    "FD": {
        "verify_templates": [
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "主流程执行成功",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "备选流程结果正确",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "element_exists", "selector": "body"},
                "expect": "页面正常",
            },
            # ★ 根因修复(2026-08-09): 字段展示验证 — 从 schema_context 提取字段名验证表格表头
            #   预期: 页面显示字段 verbose_name（如"科目名称"），而非模型 verbose_name（如"会计科目"）
            #   通用性: 跨系统/行业/页面，通过 {field_names} 占位符注入
            {
                "track": "ui",
                "precision": "exact",
                "check": {"type": "text_contains", "selector": "body"},
                "expect": "{field_names}",
            },
        ],
    },
    "PC": {
        "verify_templates": [
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "主路径执行成功",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "分支路径执行成功",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "no_error", "selector": ""},
                "expect": "无错误提示",
            },
        ],
    },
    "SD": {
        "verify_templates": [
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "规范流程执行成功",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "element_exists", "selector": "body"},
                "expect": "页面正常",
            },
        ],
    },
    "SX": {
        "verify_templates": [
            {
                "action": "fill",
                "target": "{primary_input}",
                "value": "<script>alert(1)</script>",
                "expect": "XSS脚本输入成功",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "XSS输入被转义",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "exact",
                "check": {"type": "element_exists", "selector": "body"},
                "expect": "页面未被脚本破坏",
            },
        ],
    },
    "SI": {
        "verify_templates": [
            {
                "action": "fill",
                "target": "{primary_input}",
                "value": "' OR 1=1 --",
                "expect": "SQL注入输入成功",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "SQL注入未越权",
            },
        ],
        "checks_templates": [
            {
                "track": "db",
                "precision": "exact",
                "check": {
                    "type": "db_record_count_check",
                    "table": "{primary_table}",
                    "where": "1=0",
                    "expected_count": 0,
                    "operator": "eq",
                },
                "expect": "注入条件未命中额外记录",
            },
        ],
    },
    "CS": {
        "verify_templates": [
            {
                "action": "fill",
                "target": "{primary_input}",
                "value": "{valid_value}",
                "expect": "携带防伪令牌提交成功",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "缺少防伪令牌的请求被拦截",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "exact",
                "check": {"type": "no_error", "selector": ""},
                "expect": "CSRF 校验生效无漏洞",
            },
        ],
    },
    "AU": {
        "verify_templates": [
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "未授权访问被拒绝",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "越权请求未返回受限数据",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "exact",
                "check": {"type": "no_error", "selector": ""},
                "expect": "越权控制生效无漏洞",
            },
        ],
    },
    "SS": {
        "verify_templates": [
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "登录态下访问受保护功能成功",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "登出/超时后访问受保护功能被重定向登录",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "no_error", "selector": ""},
                "expect": "会话生命周期管理正确",
            },
        ],
    },
    "CT": {
        "verify_templates": [
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "核心功能元素在目标视图下正常显示",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "页面无横向溢出、关键导航可见",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "element_exists", "selector": "body"},
                "expect": "页面正常渲染",
            },
        ],
    },
    "PF": {
        "verify_templates": [
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "页面加载后核心功能元素在阈值内可交互",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "接口响应在可接受时间内返回",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "element_exists", "selector": "body"},
                "expect": "页面正常渲染",
            },
        ],
    },
    "ET": {
        "verify_templates": [
            {
                "action": "fill",
                "target": "{primary_input}",
                "value": "非常规输入!@#$%^&*()",
                "expect": "非预期输入不导致页面崩溃",
            },
            {
                "action": "verify",
                "target": "操作结果",
                "value": "",
                "expect": "无未捕获异常报错",
            },
        ],
        "checks_templates": [
            {
                "track": "ui",
                "precision": "fuzzy",
                "check": {"type": "no_error", "selector": ""},
                "expect": "无未捕获错误",
            },
        ],
    },
}


# ── 断言精度智能解析（2026-08-09, 用户问题 #4: 断言精度问题）──
#   根因: 精度硬编码在方法论规则模板中（EP=fuzzy, BV=exact），
#   不根据 check 类型和验证目标动态调整。
#   修复: 根据 check 类型、验证目标内容自动选择最优精度级别。
#   通用性: 跨系统/行业/页面 — 精度决策基于断言语义而非业务术语。
#   软件测试工程理论：
#     - 字段级验证（字段名/枚举值）→ exact（精确匹配，假PASS容忍度=0）
#     - 页面级验证（页面加载/错误提示）→ fuzzy（页面文本可能包含额外内容）
#     - 数据验证（DB记录/API响应）→ exact（数据必须精确匹配）
#     - 安全验证（XSS/SQL注入）→ exact（安全漏洞零容忍）


def _resolve_precision(check_type: str, expect: str, track: str = "ui") -> str:
    """根据断言语义自动选择最优精度级别。

    软件测试工程理论：
    - exact: 字段名/枚举值/数据值/错误信息/安全断言 → 精确匹配
    - fuzzy: 页面文本/操作提示/通用描述 → 模糊匹配

    Args:
        check_type: 断言类型（text_contains, element_exists, no_error, db_record_exists_check 等）
        expect: 预期值文本
        track: 验证轨迹（ui/api/db）

    Returns:
        str: "exact" 或 "fuzzy"
    """
    # DB/API 轨始终精确匹配
    if track in ("db", "api"):
        return "exact"
    # 安全相关断言始终精确匹配
    if check_type in ("element_exists",) and any(
        kw in expect for kw in ("XSS", "SQL", "注入", "脚本", "越权")
    ):
        return "exact"
    # 字段名/枚举值/数据值 → exact
    # 规则: 2-6字中文且不含"功能/页面/系统/操作"等通用词
    _expect_stripped = expect.strip()
    if len(_expect_stripped) >= 2 and len(_expect_stripped) <= 8:
        _generic_kws = ("功能", "页面", "系统", "操作", "提示", "信息", "结果", "正常", "成功", "失败")
        if not any(kw in _expect_stripped for kw in _generic_kws):
            import re as _re
            # 纯中文或数字 → 精确匹配
            if _re.match(r'^[\u4e00-\u9fff0-9a-zA-Z_]+$', _expect_stripped):
                return "exact"
    # 通用文本 → fuzzy
    return "fuzzy"


# ── 占位符解析 ──

# 业务字段关键词（跨系统通用名词）：从 operation 提取字段名时优先直接命中
_FIELD_KEYWORDS = (
    "名称",
    "编码",
    "关键词",
    "关键字",
    "数量",
    "账号",
    "用户名",
    "邮箱",
    "电话",
    "手机号",
    "编号",
    "标题",
    "状态",
    "值",
    "条件",
    # ★ 通用修复(2026-09-24): 登录/鉴权/校验场景字段。缺失时"提交错误凭证登录"类
    #   operation 回退到"输入框"占位 → fill 无元素 → ERROR。加入密码/凭证/输入框，
    #   使 extract_field_from_operation 直接命中真实字段名。
    "密码",
    "凭证",
    "口令",
    "输入框",
)


def extract_field_from_operation(operation: str) -> str:
    """从 operation 提取主输入字段名（去重后缀）。

    ★ DEFECT-2 修复(2026-08-06, QA 实测):
      旧正则 `([\u4e00-\u9fff]{2,8})(?:搜索|查询|名称|编码|值|条件)` 会二次拼后缀：
      '按名称搜索辅材' → 正则 group(1)='名称' + 再拼'名称' = '名称名称'；
      '录入编码' → '录入名称'。修复：优先直接命中业务字段关键词本身，
      命中即返回（'按名称搜索辅材'→'名称'、'录入编码'→'编码'）。

    未命中返回 ""（调用方兜底"输入框"）。
    """
    if not operation:
        return ""
    for _kw in _FIELD_KEYWORDS:
        if _kw in operation:
            return _kw
    # 兜底：形如"按XXX查询/搜索"（XXX 为 2-8 字名词），直接取 XXX，不再拼后缀
    import re as _re

    _m = _re.search(r"按([\u4e00-\u9fff]{2,8})(?:搜索|查询)", operation)
    if _m:
        return _m.group(1)
    return ""


def _resolve_inputs(
    test_method: Any, explore_task: dict | None, operation: str
) -> dict[str, str]:
    """从 test_method.inputs / explore_task / operation 解析上下文占位符值。

    返回 ctx: {primary_input, valid_value, bv_min, bv_max, primary_table, op, field_names}
    """
    ctx: dict[str, str] = {"op": (operation or "").strip()}
    inputs: dict = {}
    if isinstance(test_method, dict):
        _inp = test_method.get("inputs")
        if isinstance(_inp, dict):
            inputs = _inp
    # primary_input：inputs 首个字段 → operation 提取（DEFECT-2 修复：去重后缀）→ "输入框"
    _primary_input = ""
    if inputs:
        _keys = [k for k in inputs if str(k).strip()]
        if _keys:
            _primary_input = str(_keys[0]).strip()
    if not _primary_input:
        _primary_input = extract_field_from_operation(operation)
    if not _primary_input:
        _primary_input = "输入框"
    # valid_value：inputs 首个 value → "TEST_数据"
    _valid_value = ""
    if inputs:
        _first_key = next(iter(inputs))
        _info = inputs[_first_key]
        if isinstance(_info, dict):
            _valid_value = str(_info.get("value", "") or "")
        else:
            _valid_value = str(_info or "")
    if not _valid_value:
        _valid_value = "TEST_数据"
    # bv_min：value_generator / 数字型有效值 → "0"
    _bv_min = "0"
    if _valid_value and _valid_value.isdigit():
        _bv_min = _valid_value
    ctx.update(
        {
            "primary_input": _primary_input,
            "valid_value": _valid_value,
            "bv_min": _bv_min,
            "bv_max": "999999999",
        }
    )
    # primary_table：RAG merged_data.db_tables 首个 → explore_task._db_tables → module → "数据表"
    _primary_table = ""
    if isinstance(explore_task, dict):
        _db_tables = explore_task.get("_db_tables")
        if isinstance(_db_tables, list) and _db_tables:
            _primary_table = str(_db_tables[0]).strip()
        if not _primary_table:
            _rag = explore_task.get("_rag_merged") or {}
            _rag_tables = (
                (_rag.get("db_tables") or []) if isinstance(_rag, dict) else []
            )
            if _rag_tables:
                _primary_table = str(_rag_tables[0]).strip()
        if not _primary_table:
            _module = str(explore_task.get("module") or "").strip()
            if _module:
                _primary_table = _module
    if not _primary_table:
        _primary_table = "数据表"
    ctx["primary_table"] = _primary_table
    # field_names：从 explore_task 读取（由调用方注入，如 _inject_method_and_action_templates）
    _field_names = ""
    if isinstance(explore_task, dict):
        _fn = explore_task.get("_field_verbose_names")
        if isinstance(_fn, list) and _fn:
            # 去重 + 取前 8 个，逗号拼接
            _seen_fn: set[str] = set()
            _parts_fn: list[str] = []
            for _f in _fn:
                _fs = str(_f).strip()
                if _fs and _fs not in _seen_fn:
                    _seen_fn.add(_fs)
                    _parts_fn.append(_fs)
                    if len(_parts_fn) >= 8:
                        break
            _field_names = ",".join(_parts_fn)
    ctx["field_names"] = _field_names
    return ctx


def _resolve_placeholders(template: dict, ctx: dict[str, str]) -> dict:
    """将模板中的 {占位符} 替换为上下文值（递归处理嵌套 dict/list；缺失保持原样）。"""
    out = dict(template)
    for _k, _v in list(out.items()):
        if isinstance(_v, str):
            for _ph, _val in ctx.items():
                _v = _v.replace("{%s}" % _ph, _val)
            out[_k] = _v
        elif isinstance(_v, dict):
            out[_k] = _resolve_placeholders(_v, ctx)
        elif isinstance(_v, list):
            _new_list = []
            for _item in _v:
                if isinstance(_item, dict):
                    _new_list.append(_resolve_placeholders(_item, ctx))
                elif isinstance(_item, str):
                    for _ph, _val in ctx.items():
                        _item = _item.replace("{%s}" % _ph, _val)
                    _new_list.append(_item)
                else:
                    _new_list.append(_item)
            out[_k] = _new_list
    return out


def route_method_by_intuition(
    test_method: Any,
    control_type: str = "",
    explore_task: dict | None = None,
    case_signal: str = "",
) -> str:
    """★ A2(2026-08-16): 按高级 SDET 直觉将 undecided(FD) 升级为 EP/BV/DT。

    实现抽离至 _method_router_sdet.py（避免单文件超 800 行），
    此处仅做转发，保证 _test_method_rules 内部调用签名不变。
    """
    from ._method_router_sdet import route_method_by_intuition as _route_impl

    return _route_impl(test_method, control_type, explore_task, case_signal)


def normalize_method_code(test_method: Any) -> str:
    """方法论编码归一化：'playwright'/''/None/未知 → 'FD'（场景法兜底）。

    架构师实测：DB 中 106 条用例 test_method 全为 'playwright' 或 ''（无真实编码），
    归一为 FD 后规则模板才能命中。

    ★ 根因修复(2026-09-21)：test_method 存在两种形状——字符串或 dict
      （base.py `_build_default_test_method` 返回 dict）。此前 str(dict) 会变成
      "{...}"，显式选中的方法论码（SX/SI/CS/AU...）被静默吞成 FD。现将 dict 的
      method/code 取出再归一，两种形状都能保留真实码。
    """
    if isinstance(test_method, dict):
        # dict 形状：取 method / code 键（AI/规则注入的结构化执行指南）
        test_method = test_method.get("method") or test_method.get("code") or ""
    _code = str(test_method or "").strip().upper()
    if _code in UNDECIDED_METHOD_MARKERS or not _code:
        return "FD"
    if _code in TEST_METHOD_RULES:
        return _code
    return "FD"


def canonical_method_code(
    test_method: Any,
    control_type: str = "",
    explore_task: dict | None = None,
    case_signal: str = "",
) -> str:
    """把 case 的 test_method 归一化/路由为「权威目录内唯一字符串码」。

    ★ 根因修复：此前制造商是「真实方法论模板 + 落 FD 元数据」——
      apply_method_rules 用 SDET 直觉选出 EP/BV/DT/AU… 的验证模板，却从不把该码
      写回 case['test_method']，导致写库元数据恒为 FD。此处给出唯一规范化入口，
      供 _model（模板注入）与 _modeling_engine（写库前全量兜底）统一落码：
        - 显式指定目录内真实码（含 SX/SI/CS/AU/SS/CT/PF/ET…）→ 原样保留
        - 未决定 / FD → 按 SDET 直觉（control_type/explore_task + 用例自身语义
          case_signal）升级为 EP/BV/DT/SX/SI/CS/SS/CT/PF/ET…
        - 兜底 FD
    结果必落 METHOD_CODES（权威 18 码）之内，杜绝码外杂散值。

    case_signal：用例自身语义文本（如标题/类型）。此前只吃 explore_task 的
    operation/module/反馈，导致「用例类型已是性能/兼容/安全-XSS… 而 explore_task
    无关键词」的用例坍缩为 FD（方法论用了却没落码）——并入后按自身语义确定性落码。
    """
    _raw = str(test_method or "").strip()
    if isinstance(test_method, dict):
        _raw = str(test_method.get("method") or test_method.get("code") or "").strip()
    _code = route_method_by_intuition(_raw, control_type, explore_task, case_signal)
    _code = str(_code or "").strip().upper()
    if _code not in METHOD_CODES:
        _code = "FD"
    return _code


def dedup_verifications(steps: list[dict]) -> list[dict]:
    """按 (action, target, value, expect) 去重，保留首次出现的步骤。

    规则模板内部去重用「含 value」键：BV 等模板会对同一输入先后填最小值/最大值
    （(fill, 输入框, 0) vs (fill, 输入框, 999999999)），只按 (action,target) 会误删。
    ★ A2 修复: 原键仅 (action,target,value)，导致同一模板内两条
      verify 步骤（action=verify, target=操作结果, value=""）因 value 相同被误删，
      使 EP/BV/DT 等方法模板的第二条断言（如「无效值被拦截」「上边界值处理正确」
      「条件组合2结果正确」）静默丢失，方法论名存实亡。补齐 expect 维度：
      两条 verify 步骤 expect 不同（不同测试点），应各自保留——与 merge_verifications
      的 DEFECT-1 修复（纳入 value）同一哲学，此处进一步纳入 expect。
    """
    seen: set[tuple[str, str, str, str]] = set()
    out: list[dict] = []
    for _s in steps:
        if not isinstance(_s, dict):
            continue
        _key = (
            str(_s.get("action") or "").strip(),
            str(_s.get("target") or "").strip(),
            str(_s.get("value") or "").strip(),
            str(_s.get("expect") or "").strip(),
        )
        if _key in seen:
            continue
        seen.add(_key)
        out.append(_s)
    return out


def merge_verifications(existing: list[dict], additions: list[dict]) -> list[dict]:
    """合并验证步骤：AI 生成（existing）在前，规则/动作模板（additions）追加在后。

    dedup by (action, target, value)，AI 优先（模板与 AI 完全重复的步骤跳过）。
    ★ DEFECT-1 修复(2026-08-06, QA 实测): _key 必须纳入 value——
      同一 action+target 的不同 value 是不同测试点（BV 上/下边界两次 fill 同一
      输入框），只按 (action,target) 会把上边界 999999999 误删，边界值方法论名存实亡。
    最后重排 step 序号。
    """
    _merged: list[dict] = []
    _seen: set[tuple[str, str, str]] = set()
    for _s in list(existing) + list(additions):
        if not isinstance(_s, dict):
            continue
        _key = (
            str(_s.get("action") or "").strip(),
            str(_s.get("target") or "").strip(),
            str(_s.get("value") or "").strip(),
        )
        if _key in _seen:
            continue
        _seen.add(_key)
        _merged.append(dict(_s))
    for _i, _s in enumerate(_merged, 1):
        _s["step"] = _i
    return _merged


def apply_method_rules(
    test_method: Any, control_type: str = "", explore_task: dict | None = None
) -> list[dict]:
    """按方法论编码生成确定性 verifications 模板（占位符解析 + 去重）。

    Args:
        test_method: 方法论编码字符串（'playwright'/'' 归一为 'FD'），
                     或 test_method dict（含 method/code + inputs，供占位符解析；
                     此时 inputs 优先用于 {primary_input}/{valid_value}）。
        control_type: 控件类型（保留参数，后续规则可按控件细分）。
        explore_task: explore_task 字典（提供 module/operation/_db_tables 供占位符解析）。

    Returns:
        list[dict]：解析后的 verification 步骤（未编号，由 merge_verifications 编号）。
    """
    # ★ A2(2026-08-16): undecided(FD) 按 SDET 直觉路由到 EP/BV/DT；
    #   已明确指定真实编码则原样保留（不覆盖显式决策）。
    _code = route_method_by_intuition(test_method, control_type, explore_task)
    if isinstance(test_method, dict):
        _tm = test_method
    else:
        _tm = None
    _op = ""
    if isinstance(explore_task, dict):
        _op = str(explore_task.get("operation") or "").strip()
    _ctx = _resolve_inputs(_tm or {}, explore_task, _op)
    _rule = TEST_METHOD_RULES.get(_code, TEST_METHOD_RULES["FD"])
    _resolved = [
        _resolve_placeholders(_t, _ctx) for _t in _rule.get("verify_templates", [])
    ]
    _steps = dedup_verifications([_s for _s in _resolved if isinstance(_s, dict)])
    _log.debug(
        "[TestMethodRules] code=%s control_type=%s → %d 步",
        _code,
        control_type,
        len(_steps),
    )
    return _steps


# ── 字段展示验证（I01 字段展示类型测试修复，2026-08-09）──

def generate_field_text_contains_checks(field_names: list[str]) -> list[dict]:
    """为字段展示类型测试生成精确的 text_contains 检查。

    根因修复(2026-08-09, text_contains false positive):
      Step3 生成"字段展示"测试时，expected 值使用模型 verbose_name（如"会计科目"），
      但页面表格表头显示的是字段 verbose_name（如"科目名称"）。本函数从 schema_context
      提取字段 verbose_name 列表，生成精确的 text_contains 检查，每个字段一个。

    通用性: 跨系统/行业/页面，字段名来自真实数据字典，无业务硬编码。
    覆盖: 供 _inject_method_and_action_templates 对 I01 类型测试追加字段级验证。

    Args:
        field_names: 字段 verbose_name 列表（如 ["科目名称", "科目编码", "科目类型"]）

    Returns:
        list[dict]: checks 列表，每个 check 验证一个字段名在页面中出现
    """
    if not field_names:
        return []
    _seen: set[str] = set()
    _checks: list[dict] = []
    for _fn in field_names:
        _fs = str(_fn).strip()
        if not _fs or _fs in _seen:
            continue
        _seen.add(_fs)
        _checks.append({
            "track": "ui",
            "precision": "exact",
            "check": {"type": "text_contains", "selector": "body"},
            "expect": _fs,
            "description": "验证页面表格表头包含字段: %s" % _fs,
        })
        # 最多生成 8 个字段检查，避免 checks 过长
        if len(_checks) >= 8:
            break
    return _checks


# ══════════════════════════════════════════════════════════════
# 数据流验证（DB 轨数据流分析验证，2026-08-09）
# ──────────────────────────────────────────────────────────────
# 背景事实：
#   DB轨只在Agent执行路径中标记为NOT_AVAILABLE，只检查"记录存在"或"字段值"，
#   不追踪数据从"创建→存储→展示"的完整链路。
#
# 设计（主理人决策，2026-08-09）：
#   - 确定性规则层：写操作（创建/编辑/删除）自动生成三阶段数据流 check
#   - 阶段1（UI 验证）：操作后页面显示成功提示 → text_contains: "成功"
#   - 阶段2（DB 验证）：数据已写入 DB → db_record_exists_check: "表名"
#   - 阶段3（UI 回读验证）：页面列表页展示新数据 → text_contains: "创建值"
#   - 通用性：跨系统/行业/页面 — 表名/创建值从上下文解析，无业务硬编码。
# ══════════════════════════════════════════════════════════════

_WRITE_OPERATION_KEYWORDS = (
    "创建", "新增", "添加", "新建", "编辑", "修改", "更新", "删除", "移除",
    "录入", "登记", "注册", "配置", "设置", "保存", "提交",
    "create", "add", "new", "edit", "update", "modify", "delete", "remove",
)


def is_write_operation(operation: str) -> bool:
    """检测是否为写操作类型（创建/编辑/删除）。

    数据流验证只对写操作有意义：数据从 UI 创建→DB 存储→UI 展示的完整链路。
    读操作（查询/搜索/查看/浏览）不涉及数据写入，不需要数据流验证。

    Args:
        operation: 需求操作描述（如"新增用户"、"编辑辅材"、"删除订单"）。

    Returns:
        bool: True 表示是写操作，应生成数据流验证。
    """
    if not operation:
        return False
    op_lower = operation.lower()
    return any(kw in op_lower for kw in _WRITE_OPERATION_KEYWORDS)


def generate_data_flow_checks(
    operation: str,
    explore_task: dict | None = None,
    test_method: Any = None,
) -> list[dict]:
    """为写操作生成三阶段数据流验证 checks（含数据来源+去处追踪）。

    数据流验证跟踪数据从"创建→存储→展示"的完整链路，每个阶段标注
    数据来源（source）和去处（destination），确保数据可追溯：
    - 阶段1（UI 数据录入→UI 反馈）：操作后页面显示成功提示
    - 阶段2（UI 提交→DB 存储）：数据已写入数据库
    - 阶段3（DB 查询→UI 列表展示）：页面列表页展示新数据

    Args:
        operation: 需求操作描述（如"新增用户"）。
        explore_task: 探索任务字典（提供 module/_db_tables/input_values 等上下文）。
        test_method: 测试方法 dict（提供 inputs 供占位符解析）。

    Returns:
        list[dict]: checks 列表，每个 check 验证一个数据流阶段，含 data_source/data_dest
                    字段标注数据来源和去处。非写操作返回空列表。
    """
    if not is_write_operation(operation):
        return []

    # ── 提取主表名 ──
    _primary_table = ""
    _all_tables: list[str] = []
    if isinstance(explore_task, dict):
        _db_tables = explore_task.get("_db_tables")
        if isinstance(_db_tables, list) and _db_tables:
            _primary_table = str(_db_tables[0]).strip()
            _all_tables = [str(t).strip() for t in _db_tables if str(t).strip()]
        if not _primary_table:
            _rag = explore_task.get("_rag_merged") or {}
            _rag_tables = (
                (_rag.get("db_tables") or []) if isinstance(_rag, dict) else []
            )
            if _rag_tables:
                _primary_table = str(_rag_tables[0]).strip()
                _all_tables = [str(t).strip() for t in _rag_tables if str(t).strip()]
        if not _primary_table:
            _module = str(explore_task.get("module") or "").strip()
            if _module:
                _primary_table = _module
                _all_tables = [_module]
    if not _primary_table:
        _primary_table = "数据表"
        _all_tables = ["数据表"]

    # ── 提取操作值（用于 UI 回读验证阶段3）──
    #   优先级: test_method.inputs → explore_task.input_values → "新创建的数据"
    _created_value = ""
    _input_fields: list[str] = []
    if isinstance(test_method, dict):
        _inputs = test_method.get("inputs")
        if isinstance(_inputs, dict):
            _input_fields = [str(k).strip() for k in _inputs if str(k).strip()]
            for _k, _v in _inputs.items():
                if isinstance(_v, dict):
                    _val = str(_v.get("value", "") or "")
                else:
                    _val = str(_v or "")
                if _val and _val != "TEST_数据":
                    _created_value = _val
                    break
    if not _created_value and isinstance(explore_task, dict):
        _iv = explore_task.get("input_values")
        if isinstance(_iv, dict):
            for _v in _iv.values():
                _vs = str(_v or "").strip()
                if _vs:
                    _created_value = _vs
                    break
    if not _created_value:
        _created_value = "新创建的数据"

    # ── 提取模块名（业务上下文，用于标注数据来源/去处）──
    _module_name = ""
    if isinstance(explore_task, dict):
        _module_name = str(explore_task.get("module", "") or "").strip()
    if not _module_name:
        _module_name = "当前模块"

    checks: list[dict] = []

    # 阶段1: UI 数据录入 → UI 成功提示反馈
    #   数据来源: 用户录入的字段值（{input_fields}）
    #   数据去处: 页面成功提示（"操作成功"）
    checks.append({
        "track": "ui",
        "precision": "exact",
        "check": {"type": "text_contains"},
        "expect": "成功",
        "description": "阶段1: UI 数据录入→UI 反馈: 操作后页面显示成功提示",
        "data_flow_stage": "ui_feedback",
        "data_source": "用户录入字段: %s" % (",".join(_input_fields[:5]) if _input_fields else "输入框"),
        "data_dest": "页面成功提示文本",
    })

    # 阶段2: UI 提交 → DB 存储持久化
    #   数据来源: 页面表单提交的字段值
    #   数据去处: 数据库表 {primary_table}（写入记录）
    checks.append({
        "track": "db",
        "precision": "exact",
        "check": {"type": "db_record_exists_check", "table": _primary_table},
        "expect": "数据已写入DB",
        "description": "阶段2: UI 提交→DB 存储: 数据写入表 %s" % _primary_table,
        "data_flow_stage": "db_persistence",
        "data_source": "页面表单提交的数据",
        "data_dest": "数据库表 %s（新增记录）" % _primary_table,
    })

    # 阶段3: DB 查询 → UI 列表回读展示
    #   数据来源: 数据库表 {primary_table}（查询已写入记录）
    #   数据去处: 页面列表/详情区域展示创建的数据
    checks.append({
        "track": "ui",
        "precision": "exact",
        "check": {"type": "text_contains"},
        "expect": _created_value,
        "description": "阶段3: DB 查询→UI 展示: 列表页展示新创建的数据",
        "data_flow_stage": "ui_readback",
        "data_source": "数据库表 %s 查询结果" % _primary_table,
        "data_dest": "页面列表/详情区域（展示创建值: %s）" % _created_value,
    })

    _log.info(
        "[DataFlow] 生成数据流验证: operation=%s table=%s value=%s fields=%s → %d 个 check (含数据来源/去处)",
        (operation or "")[:40],
        _primary_table[:30],
        _created_value[:20],
        ",".join(_input_fields[:3]),
        len(checks),
    )
    return checks
