# -*- coding: utf-8 -*-
"""动作模板库（T02, 2026-08-06）。

背景：AI 生成 verifications 只覆盖 fill/click/verify/wait 基础动作，
导致 double_click / right_click / press_key / select_option / check /
set_date / combo_key / scroll / hover 等动作从未被生成，Step4
_guided._exec_one_step 也无法执行（未知 action 落 else）。

本模块用确定性动作模板按 control_type/operation 补齐动作多样性，
模板生成的 action 与 Step4 _exec_one_step 新增的 9 个分支一一对应
（见 src/agent/explorer_mixins/_action.py）。

设计（主理人决策 #1/#2，2026-08-06）：
- 动作模板**始终叠加**：AI 生成 verifications 保留在前，模板追加在后，
  dedup by (action, target)，AI 优先。
- 通用性：跨系统/行业/页面 — 模板不写死业务术语。
"""

from __future__ import annotations

import logging
from typing import Any

from ._test_method_rules import dedup_verifications, extract_field_from_operation
from src.pipeline.steps.step4.action_contract import is_canonical

_log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# 8 个动作模板：模板名 → verification 步骤序列
# 占位符（与 _test_method_rules 共用约定）：
#   {primary_input}  主输入字段
#   {valid_value}    有效测试值
#   {op}             操作描述
# ══════════════════════════════════════════════════════════════
ACTION_TEMPLATES: dict[str, list[dict]] = {
    "input": [
        {
            "action": "fill",
            "target": "{primary_input}",
            "value": "{valid_value}",
            "expect": "输入成功",
        },
        {
            "action": "press_key",
            "target": "{primary_input}",
            "value": "Enter",
            "expect": "回车触发查询/提交",
        },
        {
            "action": "clear",
            "target": "{primary_input}",
            "value": "",
            "expect": "清空输入框成功",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "输入操作结果正确",
        },
    ],
    "table_row": [
        {
            "action": "hover",
            "target": "目标表格行",
            "value": "",
            "expect": "行内操作浮现",
        },
        {
            "action": "double_click",
            "target": "目标表格行",
            "value": "",
            "expect": "双击触发行操作",
        },
        {
            "action": "right_click",
            "target": "目标表格行",
            "value": "",
            "expect": "右键菜单弹出",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "表格行操作结果正确",
        },
    ],
    "select": [
        {
            "action": "select_option",
            "target": "{primary_input}",
            "value": "{valid_value}",
            "expect": "下拉选项选中成功",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "选择结果正确",
        },
    ],
    "upload": [
        {
            "action": "click",
            "target": "上传按钮",
            "value": "",
            "expect": "上传入口打开",
        },
        {
            "action": "upload_file",
            "target": "文件选择框",
            "value": "test_upload.csv",
            "expect": "文件已选择",
        },
        {"action": "wait", "target": "", "value": "2000", "expect": "等待上传完成"},
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "上传结果正确",
        },
    ],
    "checkbox": [
        {
            "action": "check",
            "target": "{primary_input}",
            "value": "",
            "expect": "复选框勾选成功",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "勾选状态正确",
        },
        {
            "action": "uncheck",
            "target": "{primary_input}",
            "value": "",
            "expect": "复选框取消勾选成功",
        },
    ],
    "date": [
        {
            "action": "set_date",
            "target": "{primary_input}",
            "value": "2026-01-01",
            "expect": "日期设置成功",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "日期值正确显示",
        },
    ],
    "combo_key": [
        {
            "action": "key_down",
            "target": "{primary_input}",
            "value": "Control",
            "expect": "组合键按下",
        },
        {
            "action": "press_key",
            "target": "{primary_input}",
            "value": "a",
            "expect": "组合键主键按下",
        },
        {
            "action": "key_up",
            "target": "{primary_input}",
            "value": "Control",
            "expect": "组合键释放",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "组合键操作结果正确",
        },
    ],
    "overlay": [
        {
            "action": "click",
            "target": "浮层触发元素",
            "value": "",
            "expect": "浮层被打开",
        },
        {
            "action": "verify",
            "target": "浮层内容",
            "value": "",
            "expect": "浮层内容显示正确",
        },
    ],
    "tooltip": [
        {
            "action": "hover",
            "target": "提示触发元素",
            "value": "",
            "expect": "提示被触发",
        },
        {
            "action": "verify",
            "target": "提示内容",
            "value": "",
            "expect": "提示内容显示正确",
        },
    ],
    "form": [
        {
            "action": "fill",
            "target": "{primary_input}",
            "value": "{valid_value}",
            "expect": "表单项填写成功",
        },
        {
            "action": "click",
            "target": "提交按钮",
            "value": "",
            "expect": "表单提交成功",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "表单提交结果正确",
        },
    ],
    "tree": [
        {
            "action": "click",
            "target": "目标树节点",
            "value": "",
            "expect": "树节点被选中",
        },
        {
            "action": "double_click",
            "target": "目标树节点",
            "value": "",
            "expect": "树节点展开",
        },
        {
            "action": "verify",
            "target": "树节点状态",
            "value": "",
            "expect": "树节点状态正确",
        },
    ],
    "steps": [
        {
            "action": "click",
            "target": "步骤操作按钮",
            "value": "",
            "expect": "步骤切换成功",
        },
        {
            "action": "verify",
            "target": "当前步骤状态",
            "value": "",
            "expect": "当前步骤状态正确",
        },
    ],
    "interactive": [
        {
            "action": "click",
            "target": "操作元素",
            "value": "",
            "expect": "触发操作元素",
        },
        {
            "action": "hover",
            "target": "操作元素",
            "value": "",
            "expect": "悬停操作元素",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "操作结果正确",
        },
    ],
    "tab_traverse": [
        {
            "action": "click",
            "target": "{primary_input}",
            "value": "",
            "expect": "焦点聚焦首个输入项",
        },
        {
            "action": "press_key",
            "target": "{primary_input}",
            "value": "Tab",
            "expect": "Tab 移动到下一个输入项",
        },
        {
            "action": "focus_check",
            "target": "{primary_input}",
            "value": "",
            "expect": "键盘 Tab 遍历后焦点命中目标项",
        },
        {
            "action": "press_key",
            "target": "{primary_input}",
            "value": "Tab",
            "expect": "再次 Tab 沿焦点序移动",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "键盘 Tab 遍历操作结果正确",
        },
    ],
    "rich_text": [
        {
            "action": "click",
            "target": "{rich_editor}",
            "value": "",
            "expect": "聚焦富文本/所见即所得编辑器",
        },
        {
            "action": "press_key",
            "target": "{rich_editor}",
            "value": "Control+a",
            "expect": "全选编辑器内已有内容",
        },
        {
            "action": "type_text",
            "target": "{rich_editor}",
            "value": "{valid_value}",
            "expect": "向富文本编辑器输入内容",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "富文本内容已更新且操作结果正确",
        },
    ],
    "text_select": [
        {
            "action": "click",
            "target": "{primary_input}",
            "value": "",
            "expect": "聚焦待处理的文本元素",
        },
        {
            "action": "press_key",
            "target": "{primary_input}",
            "value": "Control+a",
            "expect": "通过键盘全选文本",
        },
        {
            "action": "press_key",
            "target": "{primary_input}",
            "value": "Control+c",
            "expect": "复制所选文本到剪贴板",
        },
        {
            "action": "press_key",
            "target": "{primary_input}",
            "value": "Control+v",
            "expect": "粘贴剪贴板内容",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "键盘文本选择/复制粘贴操作结果正确",
        },
    ],
    "slider": [
        {
            "action": "click",
            "target": "{primary_input}",
            "value": "",
            "expect": "点击定位滑块并聚焦",
        },
        {
            "action": "drag_offset",
            "target": "{primary_input}",
            "value": "0,10",
            "expect": "按住滑块拖拽偏移调整数值",
        },
        {
            "action": "press_key",
            "target": "{primary_input}",
            "value": "ArrowRight",
            "expect": "通过键盘微调滑块数值",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "滑块数值已更新且操作结果正确",
        },
    ],
    "iframe": [
        {
            "action": "click",
            "target": "{primary_input}",
            "value": "",
            "expect": "进入 iframe 内嵌内容并聚焦目标元素",
        },
        {
            "action": "fill",
            "target": "{primary_input}",
            "value": "{valid_value}",
            "expect": "在 iframe 内填写目标输入框",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "iframe 内操作结果正确",
        },
    ],
    "autocomplete": [
        {
            "action": "click",
            "target": "{primary_input}",
            "value": "",
            "expect": "聚焦联想复合框并展开候选",
        },
        {
            "action": "type_text",
            "target": "{primary_input}",
            "value": "{valid_value}",
            "expect": "输入关键词触发联想候选",
        },
        {
            "action": "press_key",
            "target": "{primary_input}",
            "value": "Enter",
            "expect": "回车确认联想选项",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "联想选择已生效且结果正确",
        },
    ],
    "color": [
        {
            "action": "click",
            "target": "{primary_input}",
            "value": "",
            "expect": "打开取色器",
        },
        {
            "action": "fill",
            "target": "{primary_input}",
            "value": "{valid_value}",
            "expect": "输入或选择颜色值",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "颜色值已应用且显示正确",
        },
    ],
    "rating": [
        {
            "action": "click",
            "target": "{primary_input}",
            "value": "",
            "expect": "聚焦评分控件",
        },
        {
            "action": "click",
            "target": "目标评分档位",
            "value": "{valid_value}",
            "expect": "点击评分档位完成评分",
        },
        {
            "action": "press_key",
            "target": "{primary_input}",
            "value": "ArrowRight",
            "expect": "键盘微调评分值",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "评分已更新且结果正确",
        },
    ],
    "scroll_hover": [
        {
            "action": "scroll",
            "target": "页面",
            "value": "500",
            "expect": "页面滚动成功",
        },
        {
            "action": "hover",
            "target": "目标菜单项",
            "value": "",
            "expect": "悬停展开子菜单",
        },
        {
            "action": "verify",
            "target": "操作结果",
            "value": "",
            "expect": "滚动悬停结果正确",
        },
    ],
}

# control_type → 动作模板映射（跨系统通用控件类别）
CONTROL_TYPE_TO_TEMPLATE: dict[str, str] = {
    "input": "input",
    "text": "input",
    "number": "input",
    "password": "input",
    "email": "input",
    "textarea": "input",
    "rich_text": "rich_text",
    "editor": "rich_text",
    "contenteditable": "rich_text",
    "richtext": "rich_text",
    "search": "input",
    "date_input": "input",
    "select": "select",
    "cascader": "select",
    "transfer": "select",
    "dropdown": "select",
    "radio": "checkbox",
    "checkbox": "checkbox",
    "switch": "checkbox",
    "toggle": "checkbox",
    "date": "date",
    "datetime": "date",
    "time": "date",
    "datepicker": "date",
    "upload": "upload",
    "dragger": "upload",
    "file": "upload",
    "file_input": "upload",
    "table": "table_row",
    "list": "table_row",
    "display": "table_row",
    "grid": "table_row",
    "nav": "scroll_hover",
    "menu": "scroll_hover",
    "tabs": "scroll_hover",
    "breadcrumb": "scroll_hover",
    "pagination": "scroll_hover",
    "interactive": "interactive",
    "modal": "overlay",
    "drawer": "overlay",
    "tooltip": "tooltip",
    "popconfirm": "overlay",
    "composite": "interactive",
    "form": "form",
    "tree": "tree",
    "steps": "steps",
    "slider": "slider",
    "range": "slider",
    "slider_range": "slider",
    "iframe": "iframe",
    "frame": "iframe",
    "embedded": "iframe",
    "autocomplete": "autocomplete",
    "combobox": "autocomplete",
    "color": "color",
    "colorpicker": "color",
    "rating": "rating",
    "rate": "rating",
    "stars": "rating",
}

# operation 关键词 → 动作模板（优先于 control_type，命中即用）
_OPERATION_TO_TEMPLATE: tuple[tuple[tuple[str, ...], str], ...] = (
    (("Iframe", "iframe", "内嵌页面", "内嵌内容", "嵌入页面", "嵌入式", "frame"), "iframe"),
    (("滑块", "slider", "进度条拖动", "拖动调节"), "slider"),
    (("自动补全", "自动联想", "autocomplete", "智能联想"), "autocomplete"),
    (("取色", "颜色选择", "选择颜色", "colorpick", "color"), "color"),
    (("评分", "星级评分", "rating", "rate"), "rating"),
    (("双击", "double click", "dblclick"), "table_row"),
    (("右键", "right click", "contextmenu"), "table_row"),
    (("上传", "upload", "导入文件", "选择文件"), "upload"),
    (("复制", "粘贴", "剪贴板", "文本选择", "select text", "select all", "copy", "cut"), "text_select"),
    (("勾选", "选中", "复选", "check", "勾上"), "checkbox"),
    (("选择日期", "日期", "date"), "date"),
    (("组合键", "快捷键", "ctrl", "control", "快捷键组合"), "combo_key"),
    (("富文本", "编辑器", "contenteditable", "rich text", "rich_text", "所见即所得", "图文编辑"), "rich_text"),
    (("Tab", "tab", "焦点", "键盘", "可达性", "focus"), "tab_traverse"),
    (("滚动", "scroll", "悬停", "hover"), "scroll_hover"),
    (("下拉", "选择", "select", "option"), "select"),
    (("输入", "填写", "录入", "fill", "search", "搜索", "查询"), "input"),
)


def _match_template(control_type: str, operation: str) -> str:
    """确定命中的动作模板：operation 关键词优先 → control_type 映射 → input 兜底。"""
    _op_lower = (operation or "").lower()
    for _kws, _tpl in _OPERATION_TO_TEMPLATE:
        if any(_kw in _op_lower for _kw in _kws):
            return _tpl
    _ct = str(control_type or "").strip().lower()
    if _ct in CONTROL_TYPE_TO_TEMPLATE:
        return CONTROL_TYPE_TO_TEMPLATE[_ct]
    return "input"


def _primary_input_from_operation(operation: str) -> str:
    """从 operation 提取主输入字段名（★ DEFECT-2 修复：去重后缀）。

    '按名称搜索辅材' → '名称'（不再'名称名称'）；'录入编码' → '编码'。
    未命中返回 "输入框"。
    """
    if not operation:
        return "输入框"
    return extract_field_from_operation(operation) or "输入框"


def apply_action_templates(
    control_type: str, operation: str, test_method: Any = None
) -> list[dict]:
    """按 control_type/operation 生成动作模板 verifications（占位符解析 + 去重）。

    Args:
        control_type: 控件类型（input/select/upload/checkbox/date/table/...）。
        operation: 需求操作描述（用于关键词匹配 + 占位符解析）。
        test_method: test_method dict（提供 inputs 供占位符解析；可为 None）。

    Returns:
        list[dict]：模板 verification 步骤（未编号，由 merge_verifications 编号）。
    """
    _tpl_name = _match_template(control_type, operation)
    _tpl = ACTION_TEMPLATES.get(_tpl_name, ACTION_TEMPLATES["input"])
    # 占位符上下文
    _ctx = {"op": (operation or "").strip()}
    _inputs: dict = {}
    if isinstance(test_method, dict):
        _inp = test_method.get("inputs")
        if isinstance(_inp, dict):
            _inputs = _inp
    _primary_input = ""
    if _inputs:
        _keys = [k for k in _inputs if str(k).strip()]
        if _keys:
            _primary_input = str(_keys[0]).strip()
    if not _primary_input:
        _primary_input = _primary_input_from_operation(operation)
    _valid_value = "TEST_数据"
    if _inputs:
        _first_key = next(iter(_inputs))
        _info = _inputs[_first_key]
        if isinstance(_info, dict):
            _valid_value = str(_info.get("value", "") or "") or _valid_value
        else:
            _valid_value = str(_info or "") or _valid_value
    _ctx["primary_input"] = _primary_input
    _ctx["valid_value"] = _valid_value
    _resolved = []
    for _t in _tpl:
        _s = dict(_t)
        for _k, _v in list(_s.items()):
            if isinstance(_v, str):
                for _ph, _val in _ctx.items():
                    _v = _v.replace("{%s}" % _ph, _val)
                _s[_k] = _v
        _resolved.append(_s)
    _steps = dedup_verifications(_resolved)
    # ★ 契约统一：模板产出的 action 必须都在统一契约白名单内，否则告警（防静默漂移）
    for _s in _steps:
        _a = str(_s.get("action", "") or "").strip()
        if _a and not is_canonical(_a):
            _log.warning(
                "[ActionContract] 模板 %s 产出契约外 action=%r —— 生成层与执行层契约不统一",
                _tpl_name,
                _a,
            )
    _log.debug(
        "[ActionTemplates] control_type=%s operation=%s → 模板=%s (%d 步)",
        control_type,
        (operation or "")[:30],
        _tpl_name,
        len(_steps),
    )
    return _steps
