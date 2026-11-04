"""验证点时序分类契约测试。

根因: guided 执行此前只有 Pre/Post 二分 —— 目标含"页面内容/页面特征"归 Pre，
其余全归 Post。「进入子视图」型检查（"点击新增按钮后表单/弹窗打开"）不匹配
Pre 关键词 → 落 Post → 在填表并提交之后才执行（页面已回列表页）→ 必然未命中
→ 用例假 ERROR。检查的时序位置应由它在动作链中的**语义位置**决定。
"""

from __future__ import annotations

import pytest

from src.agent.explorer_mixins._action import _is_entry_state_check


@pytest.mark.parametrize(
    "target,expect",
    [
        ("新增表单", "点击新增按钮后表单/弹窗打开"),
        ("编辑弹窗", "点击编辑后弹窗打开"),
        ("模态框", "模态框出现"),
        ("dialog", "form dialog opened"),
        ("新增抽屉", "点击新增后抽屉展开"),
    ],
)
def test_entry_state_check_accepts_subview_open(target, expect):
    """子视图名词 + 打开类状态词 → 属「进入子视图」检查。"""
    assert _is_entry_state_check(target, expect) is True


@pytest.mark.parametrize(
    "target,expect",
    [
        ("列表记录", "列表含唯一值 X 的记录"),
        ("校验提示", "提交后出现字段级错误提示或成功落地记录"),
        ("页面内容", "页面正常加载"),
        ("操作结果", "删除成功"),
        ("搜索结果", "显示搜索结果"),
    ],
)
def test_entry_state_check_rejects_result_checks(target, expect):
    """结果类检查（列表/记录/成功/提示）不得被误收为 entry。"""
    assert _is_entry_state_check(target, expect) is False


def test_entry_state_check_requires_noun():
    """只有状态词、无子视图名词 → 不是 entry 检查。"""
    assert _is_entry_state_check("结果", "操作后打开成功页") is False


def test_entry_state_check_target_result_wins():
    """target 本身是结果语义时，即使含子视图名词也不判 entry（防误收）。"""
    assert _is_entry_state_check("表单列表记录", "列表显示表单记录") is False
