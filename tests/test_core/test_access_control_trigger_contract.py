# -*- coding: utf-8 -*-
"""契约：访问控制断言不得被"前置交互补全"改写动作。

目的（它修的是哪个既有缺陷）：
  `ensure_post_action_triggers` 对判定为"需交互"的用例统一注入
  `build_field_validation_trigger`（click 录入入口 → fill 非法值 → click 提交）。
  `access_control_check` 的可达前提是「身份 + 入口导航」，不是表单交互；其断言
  文本常含"重定向/拒绝"而被交互后果词误命中，导致被注入与语义无关的表单动作
  → 结构性必 FAIL。本契约锁定：该断言类型的"是否需交互"判定恒为 False。
"""

from __future__ import annotations


def test_access_control_check_never_requires_form_interaction() -> None:
    """访问控制断言的判定入口恒为 False —— 无论期望文本含何交互后果词。"""
    from src.pipeline.step3._trigger_actions import check_requires_interaction

    texts = (
        "低权身份执行受限操作被系统拒绝（403/权限不足/重定向登录）",
        "未登录访问须重定向登录页",
        "系统拒绝访问并返回403权限不足",
        "访问审计日志页被拒绝并跳转登录",
    )
    for txt in texts:
        assert check_requires_interaction("access_control_check", txt) is False, (
            "access_control_check 的可达条件为『身份+入口导航』而非表单交互，"
            "判定必须为 False，否则会被注入与语义无关的表单提交动作"
        )


def test_access_control_check_registered_in_readonly_source() -> None:
    """判定须来自唯一事实源 `_NATURALLY_READONLY_CHECK_TYPES`，不得在别处另判。"""
    from src.pipeline.step3._trigger_actions import _NATURALLY_READONLY_CHECK_TYPES

    assert "access_control_check" in _NATURALLY_READONLY_CHECK_TYPES


def test_field_validation_and_outcome_checks_unaffected() -> None:
    """不误伤：字段校验类与交互后果类断言的判定保持原样。"""
    from src.pipeline.step3._trigger_actions import check_requires_interaction

    assert check_requires_interaction("field_error_visible_check", "非空错误提示文本") is True
    assert check_requires_interaction("semantic_check", "系统提示保存成功") is True
    assert check_requires_interaction("semantic_check", "系统跳转到列表页") is True
    assert check_requires_interaction("loading_complete_check", "加载完成") is False
