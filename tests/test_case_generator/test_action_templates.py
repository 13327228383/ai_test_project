# -*- coding: utf-8 -*-
"""单元测试：动作模板映射摆脱"假覆盖"——复杂控件不再统一落到 combo_key(Control+A)。

镜像 src/pipeline/steps/step3/case_generator/_action_templates.py。
Corresponds to the通用性=键盘鼠标控件组合修复：
- modal/drawer/popconfirm → overlay：click 打开 + verify 可见
- tooltip → tooltip：hover 触发 + verify 提示
- form → form：fill + click 提交 + verify
- tree → tree：click/double_click 节点 + verify
- steps → steps：click 下一步 + verify
- composite/interactive → interactive：click + hover + verify
- combo_key 仅当需求显式含"组合键/快捷键/ctrl/control"时命中
"""

import pytest

from src.pipeline.steps.step3.case_generator import _action_templates as at
from src.pipeline.steps.step4 import action_contract as ac


# 不复用 combo_key 的控件（曾 8 类共用 Control+A 假覆盖）
@pytest.mark.parametrize(
    "control_type,expected_tpl",
    [
        ("modal", "overlay"),
        ("drawer", "overlay"),
        ("popconfirm", "overlay"),
        ("tooltip", "tooltip"),
        ("form", "form"),
        ("tree", "tree"),
        ("steps", "steps"),
        ("composite", "interactive"),
        ("interactive", "interactive"),
    ],
)
def test_complex_controls_no_longer_map_to_combo_key(control_type, expected_tpl):
    assert at._match_template(control_type, "") == expected_tpl


def test_combo_key_only_reaches_when_requirement_explicitly_asks():
    # 显式组合键意图仍生成 combo_key（Ctrl+A）
    assert at._match_template("form", "按 Ctrl+A 全选") == "combo_key"
    assert at._match_template("modal", "使用快捷键打开") == "combo_key"
    # 普通 modal/form 不带组合键关键词 → 走各自控件模板
    assert at._match_template("modal", "") != "combo_key"
    # form 控件无动词关键词命中（如纯"提交"）→ 走 form 模板；含"录入/输入"则走 input（操作优先，属预期）
    assert at._match_template("form", "提交表单") == "form"


def test_new_templates_exist_in_action_templates():
    for name in ("overlay", "tooltip", "form", "tree", "steps", "interactive"):
        assert name in at.ACTION_TEMPLATES, name
    # combo_key 模板保留（显式组合键仍可用）
    assert "combo_key" in at.ACTION_TEMPLATES


def test_apply_action_templates_overlay_uses_click_verify():
    steps = at.apply_action_templates("modal", "查看详情")
    actions = [s.get("action") for s in steps]
    assert "click" in actions
    assert "verify" in actions
    assert "key_down" not in actions  # 不再是组合键假覆盖
    assert not any(s.get("value") == "Control" for s in steps)


def test_apply_action_templates_form_fills_and_submits():
    steps = at.apply_action_templates("form", "提交表单")
    actions = [s.get("action") for s in steps]
    assert "fill" in actions
    assert "click" in actions
    assert "verify" in actions


def test_apply_action_templates_tooltip_uses_hover():
    steps = at.apply_action_templates("tooltip", "查看提示")
    actions = [s.get("action") for s in steps]
    assert "hover" in actions
    assert "verify" in actions


# ──────────────────────────────────────────────────────────────
# 键盘 Tab 遍历 + 契约统一
# ──────────────────────────────────────────────────────────────
def test_tab_traverse_matches_keyboard_operation():
    # 显式键盘/焦点意图命中 tab_traverse（与 combo_key 区分）
    assert at._match_template("form", "按 Tab 键遍历输入项") == "tab_traverse"
    assert at._match_template("form", "验证键盘可达性") == "tab_traverse"
    # 普通表单操作走 form（不误触发 tab_traverse）
    assert at._match_template("form", "提交表单") == "form"
    assert at._match_template("form", "按 Ctrl+A 全选") == "combo_key"


def test_tab_traverse_emits_focus_check_and_press_key():
    steps = at.apply_action_templates("form", "按 Tab 键遍历输入项")
    actions = [s.get("action") for s in steps]
    assert "focus_check" in actions
    assert "press_key" in actions
    assert actions.count("press_key") >= 2  # 至少两次 Tab 移动


def test_all_template_actions_are_within_contract():
    """契约统一底线：所有模板产出的 action 必须都在统一契约白名单内。"""
    for _tpl_name, _seq in at.ACTION_TEMPLATES.items():
        for _s in _seq:
            _a = str(_s.get("action", "") or "").strip()
            assert ac.is_canonical(_a), "模板 %s 产出契约外 action=%r" % (_tpl_name, _a)


def test_contract_classify_and_gap():
    # GUIDED
    assert ac.classify_action("click") == "GUIDED"
    assert ac.classify_action("focus_check") == "GUIDED"
    # 契约 GAP 修复后：drag/wheel/multi_touch/download_file 走引导路径直执行
    assert ac.classify_action("drag") == "GUIDED"
    assert ac.classify_action("wheel") == "GUIDED"
    assert ac.classify_action("multi_touch") == "GUIDED"
    assert ac.classify_action("download_file") == "GUIDED"
    assert ac.guided_gap("drag") is False
    # UNKNOWN
    assert ac.classify_action("no_such_action") == "UNKNOWN"
    assert ac.is_canonical("no_such_action") is False
    assert ac.is_canonical("") is False


# ──────────────────────────────────────────────────────────────
# 通用-复杂控件模板：富文本 / 纯键盘文本选择·复制粘贴
# ──────────────────────────────────────────────────────────────
def test_rich_text_matches_template():
    # operation 关键词命中富文本（优先于通用 input）
    assert at._match_template("rich_text", "") == "rich_text"
    assert at._match_template("editor", "在富文本编辑器输入内容") == "rich_text"
    assert at._match_template("contenteditable", "打开所见即所得编辑器") == "rich_text"
    # 普通控件不带富文本关键词 → 不误触发
    assert at._match_template("textarea", "提交文本") != "rich_text"


def test_text_select_matches_template():
    # 复制/粘贴/剪贴板等键盘文本操作 → text_select
    assert at._match_template("textarea", "复制并粘贴选中内容") == "text_select"
    assert at._match_template("text", "全选的文本复制到剪贴板") == "text_select"
    # 显式 Ctrl+A(组合键)仍归 combo_key；富文本不归 text_select
    assert at._match_template("form", "按 Ctrl+A 全选") == "combo_key"


def test_rich_text_emits_type_verify_press():
    steps = at.apply_action_templates("rich_text", "富文本")
    actions = [s.get("action") for s in steps]
    assert "click" in actions
    assert "press_key" in actions
    assert "type_text" in actions
    assert "verify" in actions


def test_text_select_emits_press_keys():
    steps = at.apply_action_templates("textarea", "复制粘贴")
    actions = [s.get("action") for s in steps]
    # 纯键盘：全选 Ctrl+A → 复制 Ctrl+C → 粘贴 Ctrl+V
    assert all(a == "press_key" for a in
               [a for a in actions if a != "click" and a != "verify"])
    assert actions.count("press_key") >= 3


# ──────────────────────────────────────────────────────────────
# 通用-复杂控件模板：滑块 slider / 内嵌 iframe
# ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "control_type,expected_tpl",
    [
        ("slider", "slider"),
        ("range", "slider"),
        ("slider_range", "slider"),
        ("iframe", "iframe"),
        ("frame", "iframe"),
        ("embedded", "iframe"),
    ],
)
def test_slider_and_iframe_map_to_dedicated_template(control_type, expected_tpl):
    # 不再回落 input 兜底（避免把滑块当输入框 fill 的假覆盖）
    assert at._match_template(control_type, "") == expected_tpl


def test_slider_operation_keyword_hits_slider():
    # 需求显式含"滑块/进度条拖动" → slider（操作优先于控件映射）
    assert at._match_template("", "拖动滑块调整数值") == "slider"
    assert at._match_template("range", "拖动进度条调节音量") == "slider"
    # 泛化操作不带滑块关键词 → 不误触发
    assert at._match_template("", "输入数值") != "slider"


def test_iframe_operation_keyword_hits_iframe():
    # 需求显式含"内嵌页面/iframe" → iframe
    assert at._match_template("", "在 iframe 内填写账号") == "iframe"
    assert at._match_template("", "进入嵌入式页面") == "iframe"
    assert at._match_template("contenteditable", "在富文本编辑器输入") != "iframe"


def test_slider_emits_drag_and_key_verify():
    steps = at.apply_action_templates("slider", "拖动滑块调整数值")
    actions = [s.get("action") for s in steps]
    assert "click" in actions
    # 滑块靠拖拽偏移调整（引导路径已实现 drag_offset），并有键盘微调
    assert "drag_offset" in actions
    assert "press_key" in actions
    assert "verify" in actions
    # 不是组合键/输入框假覆盖
    assert "key_down" not in actions
    assert "fill" not in actions


def test_iframe_emits_click_fill_verify():
    steps = at.apply_action_templates("iframe", "在 iframe 内填写账号")
    actions = [s.get("action") for s in steps]
    assert "click" in actions
    assert "fill" in actions
    assert "verify" in actions


def test_slider_iframe_within_contract():
    # 契约底线：新模板所有 action 均在统一契约白名单
    for name in ("slider", "iframe"):
        for _s in at.ACTION_TEMPLATES[name]:
            assert ac.is_canonical(str(_s.get("action", "") or "").strip()), (
                "%s 模板产出契约外 action=%r" % (name, _s.get("action"))
            )


# ──────────────────────────────────────────────────────────────
# 通用-复杂控件模板（二）：autocomplete / color / rating
# ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "control_type,expected_tpl",
    [
        ("autocomplete", "autocomplete"),
        ("combobox", "autocomplete"),
        ("color", "color"),
        ("colorpicker", "color"),
        ("rating", "rating"),
        ("rate", "rating"),
    ],
)
def test_new_complex_controls_map_to_dedicated_template(control_type, expected_tpl):
    # 不再回落 input/通用 兜底（避免假覆盖）
    assert at._match_template(control_type, "") == expected_tpl


def test_operation_keywords_for_new_controls():
    assert at._match_template("", "自动补全城市") == "autocomplete"
    assert at._match_template("", "选择颜色") == "color"
    assert at._match_template("", "给商品评分") == "rating"


def test_autocomplete_emits_type_and_enter():
    steps = at.apply_action_templates("autocomplete", "自动补全城市")
    actions = [s.get("action") for s in steps]
    assert "type_text" in actions
    assert "press_key" in actions
    assert actions.count("press_key") >= 1
    assert "verify" in actions


def test_color_emits_fill_verify():
    steps = at.apply_action_templates("color", "选择颜色")
    actions = [s.get("action") for s in steps]
    assert "fill" in actions
    assert "verify" in actions
    # 取色并非普通输入框 → 不落入 input 模板（无 press Enter 依赖）
    assert at._match_template("color", "") == "color"


def test_rating_emits_click_and_key():
    steps = at.apply_action_templates("rating", "给商品评分")
    actions = [s.get("action") for s in steps]
    assert actions.count("click") >= 2  # 聚焦 + 评分档位
    assert "press_key" in actions
    assert "verify" in actions


def test_new_templates_within_contract():
    # 契约底线：新模板所有 action 均在统一契约白名单
    for name in ("autocomplete", "color", "rating"):
        for _s in at.ACTION_TEMPLATES[name]:
            assert ac.is_canonical(str(_s.get("action", "") or "").strip()), (
                "%s 模板产出契约外 action=%r" % (name, _s.get("action"))
            )