# -*- coding: utf-8 -*-
"""
控件类型识别 + 测试方法论智能选择（07-05h 新增）
================================================

按 operation 关键词识别控件类型，根据控件类型智能选择适用的测试方法论。

设计：
  - 纯算法识别（无AI调用）
  - 8 大控件类：input/select/button/upload/display/nav/interactive/composite
  - 每类控件匹配适用的测试方法论编码（EP/BV/CE/DT/ST/OT/EG/FD/PC/SD/SX/SI）
  - 用于 Step3 user_message 注入控件类型 + 测试方法论指导

核心方法：
  - detect_control_type(operation, expected_feedback) → str
  - recommend_test_methods(control_type) → list[str]
  - build_control_method_hint(operation, expected_feedback) → str
"""

import re
import logging

_log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════
# 控件类型识别规则（按 operation 关键词）
# ══════════════════════════════════════════════════

# 按优先级排序（先匹配先返回）
# ★ 优先级原则：具体 > 通用（如"打开弹窗"比"新增"具体，"提交表单"比"提交"具体）
_CONTROL_TYPE_RULES = [
    # composite（复合类）— 最高优先级（多步骤流程，含"表单"关键词）
    (
        "composite",
        re.compile(
            r"提交表单|多步骤|多步流程|向导|树形|树节点|拖拽排序|批量操作|注册表单|登录表单",
            re.IGNORECASE,
        ),
    ),
    # upload（上传类）
    ("upload", re.compile(r"上传|拖拽上传|导入文件|附件上传|文件选择", re.IGNORECASE)),
    # interactive（交互类）— 在 button 之前匹配（"打开弹窗"含"打开"）
    (
        "interactive",
        re.compile(
            r"打开弹窗|关闭弹窗|弹出|抽屉|悬浮提示|气泡确认|模态框|dialog|modal|drawer|tooltip|popconfirm|新增.*弹窗|编辑.*弹窗",
            re.IGNORECASE,
        ),
    ),
    # ★ P0-downgrade-removal(2026-07-29): display 控件类型已删除，降级=失败
    # nav（导航类）— 不含"登录/注册"（避免与 button 冲突）
    (
        "nav",
        re.compile(
            r"跳转|导航|切换页|进入页面|返回上一页|菜单切换|面包屑|分页|tab页切换|标签页切换",
            re.IGNORECASE,
        ),
    ),
    # select（选择类）
    (
        "select",
        re.compile(
            r"选择|勾选|下拉|单选|多选|切换开关|cascader|级联|transfer|穿梭框",
            re.IGNORECASE,
        ),
    ),
    # button（按钮类）— 含"点击/保存/删除/登录"等动词
    (
        "button",
        re.compile(
            r"点击|点击.*按钮|保存按钮|提交按钮|确认按钮|取消按钮|删除按钮|新增按钮|导出按钮|搜索按钮|筛选按钮|重置按钮|审批按钮|审核按钮|批准|驳回|签署|签订|登录按钮|退出按钮|注销按钮|登录|退出|注销|保存|提交|确认|取消|删除|新增|导出|搜索|筛选|重置",
            re.IGNORECASE,
        ),
    ),
    # input（输入类）— 兜底匹配（含文本输入）
    (
        "input",
        re.compile(
            r"输入|填写|录入|编辑|设置(?!.*开关)|修改(?!.*状态)|密码|用户名|账号|邮箱|手机号|姓名|地址|描述|备注|金额|数量|价格|日期|时间",
            re.IGNORECASE,
        ),
    ),
]


# ══════════════════════════════════════════════════
# 控件类型 → 适用测试方法论映射
# ══════════════════════════════════════════════════

_CONTROL_TYPE_METHODS = {
    "input": ["EP", "BV", "EG", "SX", "SI", "SD"],
    "select": ["EP", "EG", "SD"],
    "button": ["FD", "CE", "SD"],
    "upload": ["EP", "BV", "EG", "SX"],
    "nav": ["FD", "ST", "PC"],
    "interactive": ["FD", "ST", "EG"],
    "composite": ["FD", "CE", "DT", "ST", "PC"],
}

# 不适用的方法论（用于Prompt提醒AI避免）
_CONTROL_TYPE_EXCLUDED = {
    "input": ["DT", "ST", "OT", "PC"],
    "select": ["BV", "SX", "SI", "DT"],
    "button": ["BV", "SX", "SI", "EP"],
    "upload": ["DT", "ST", "OT", "PC", "SI"],
    "nav": ["BV", "SX", "SI", "EP"],
    "interactive": ["BV", "SX", "SI"],
    "composite": ["BV", "SX", "SI"],
}


def detect_control_type(operation: str, expected_feedback: str = "") -> str:
    """识别控件类型（纯算法，按 operation 关键词匹配）。

    Args:
        operation: 操作描述（如"用户点击登录按钮"）
        expected_feedback: 预期反馈（仅作辅助参考，不参与主匹配，
            避免"跳转主页"这类反馈结果误触发 nav 规则）

    Returns:
        控件类型编码：input/select/button/upload/display/nav/interactive/composite
        未匹配返回 "button"（默认，最常见）
    """
    if not operation:
        return "button"

    # ★ 只用 operation 做主匹配（expected_feedback 是结果，不是操作）
    text = operation

    for ctrl_type, pattern in _CONTROL_TYPE_RULES:
        if pattern.search(text):
            return ctrl_type

    # 兜底：默认 button（最常见的操作类型）
    return "button"


def recommend_test_methods(control_type: str) -> list[str]:
    """根据控件类型推荐适用的测试方法论编码列表。

    Args:
        control_type: 控件类型编码

    Returns:
        测试方法论编码列表（如 ["EP", "BV", "EG", "SX", "SI", "SD"]）
        未知控件类型返回 ["FD", "SD"]（场景法+规范导出，最通用）
    """
    return _CONTROL_TYPE_METHODS.get(control_type, ["FD", "SD"])


def get_excluded_methods(control_type: str) -> list[str]:
    """获取不适用的测试方法论（用于提醒AI避免）。

    Args:
        control_type: 控件类型编码

    Returns:
        不适用的方法论编码列表
    """
    return _CONTROL_TYPE_EXCLUDED.get(control_type, [])


def build_control_method_hint(operation: str, expected_feedback: str = "") -> str:
    """构造控件类型+测试方法论的Prompt提示文本。

    Args:
        operation: 操作描述
        expected_feedback: 预期反馈

    Returns:
        Prompt 提示文本，如：
        "★ 控件类型: input(输入类)
         ★ 适用方法论: EP+BV+EG+SX+SI+SD
         ★ 不适用方法论: DT/ST/OT/PC（不要生成这些类型的用例）"
    """
    ctrl_type = detect_control_type(operation, expected_feedback)
    methods = recommend_test_methods(ctrl_type)
    excluded = get_excluded_methods(ctrl_type)

    ctrl_type_names = {
        "input": "输入类(text/number/password等)",
        "select": "选择类(select/radio/checkbox等)",
        "button": "按钮类(button/submit等)",
        "upload": "上传类(upload/dragger)",
        "nav": "导航类(menu/tabs/pagination等)",
        "interactive": "交互类(modal/drawer/tooltip等)",
        "composite": "复合类(form/Steps/Tree等)",
    }
    ctrl_name = ctrl_type_names.get(ctrl_type, ctrl_type)

    hint = (
        f"★ 控件类型: {ctrl_type}({ctrl_name})\n" f"★ 适用方法论: {'+'.join(methods)}\n"
    )
    if excluded:
        hint += f"★ 不适用方法论: {'/'.join(excluded)}（不要生成这些类型的用例）\n"
    return hint


# ══════════════════════════════════════════════════
# 测试方法论编码 → 中文名 映射（供前端展示）
# ══════════════════════════════════════════════════

TEST_METHOD_NAMES = {
    "EP": "等价类划分",
    "BV": "边界值分析",
    "CE": "因果图",
    "DT": "判定表",
    "ST": "状态迁移",
    "OT": "正交实验",
    "EG": "错误推测",
    "FD": "场景法",
    "PC": "路径覆盖",
    "SD": "规范导出",
    "SX": "XSS注入",
    "SI": "SQL注入",
}

CONTROL_TYPE_NAMES = {
    "input": "输入类",
    "select": "选择类",
    "button": "按钮类",
    "upload": "上传类",
    "nav": "导航类",
    "interactive": "交互类",
    "composite": "复合类",
}
