# -*- coding: utf-8 -*-
"""D59 契约：用例「期望被拒绝」语义必须是单源（一写多读）。

背景（缺陷形状）
----------------
同一语义「本用例断言期望被拒绝/不可达」曾在多点被各自重新推导：
  * ``browser_tools/execution/_single_nav_state.py::_check_error_page`` 里另行拼词表；
  * ``agent/explorer_mixins/_action.py`` 的 404 分支再拼一份词表。
后果：期望 403 的访问控制用例被 ``_check_error_page`` 判为"页面错误" →
触发重登 + 菜单回退重试 → 单条白烧 37.9s 后判假 FAIL（错误数 > 通过数的直接来源之一）。

契约（本测试锁定）
------------------
1. 唯一生产函数 = ``step3._identity_assign.case_expects_denial``；本模块不复述判据。
2. 唯一写入点 = ``step4/browser_core/single_mixins/_base.py::_execute_one_intent``。
3. 读取点只经 ``src/core/case_context.py``，不得自持第二份拒绝词表。
4. ``_check_error_page`` 在 ``expects_denial=True`` 时，拒绝类响应不得判为错误页。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CASE_CONTEXT = ROOT / "src" / "core" / "case_context.py"
NAV_STATE = (
    ROOT / "src" / "pipeline" / "steps" / "step4" / "browser_tools"
    / "execution" / "_single_nav_state.py"
)
BASE_MIXIN = (
    ROOT / "src" / "pipeline" / "steps" / "step4" / "browser_core"
    / "single_mixins" / "_base.py"
)
ACTION = ROOT / "src" / "agent" / "explorer_mixins" / "_action.py"


def _src(path: Path) -> str:
    assert path.exists(), f"缺失文件: {path}"
    return path.read_text(encoding="utf-8")


def test_case_context_exposes_single_write_and_read_point():
    """上下文模块只暴露一个写入口 + 一个读取口。"""
    src = _src(CASE_CONTEXT)
    assert "def set_case_expects_denial(" in src
    assert "def current_case_expects_denial(" in src
    assert "contextvars.ContextVar" in src, "必须用 ContextVar 隔离并行用例"


def test_nav_state_delegates_to_case_context():
    """`_check_error_page` 默认走单源上下文，且不复述拒绝词表判据。"""
    src = _src(NAV_STATE)
    assert "current_case_expects_denial" in src, "必须读单源上下文"
    # 不得在读取处重新按 expect 文本猜拒绝语义（那是生产函数的职责）
    assert "_expect_lower" not in src


def test_write_point_is_call_before_navigation():
    """唯一写入点位于意图入口，且在首次导航之前。"""
    src = _src(BASE_MIXIN)
    assert "set_case_expects_denial(" in src
    assert "case_expects_denial(" in src, "必须复用唯一生产函数"
    idx_write = src.index("set_case_expects_denial(")
    idx_title = src.index("self._current_case_id = case_id")
    assert idx_write > idx_title, "写入点必须紧跟用例上下文建立"


def test_action_layer_has_no_second_denial_wordlist():
    """`_action.py` 不得自持第二份拒绝关键词表。"""
    src = _src(ACTION)
    assert "current_case_expects_denial" in src, "404 分支必须读单源上下文"
    assert "_expect_lower_404" not in src, "第二份词表推导必须已删除"


def test_expected_denial_page_not_treated_as_error():
    """期望被拒绝时，403 页面不得判为错误页（否则重登+回退 → 假 FAIL）。"""
    from src.core import case_context

    class _L:
        def __init__(self, text: str) -> None:
            self._text = text

        async def count(self) -> int:
            return 0

        async def inner_text(self) -> str:
            return self._text

    class _Page:
        def __init__(self, text: str) -> None:
            self._text = text

        def locator(self, _sel: str):
            return _L(self._text)

    from src.pipeline.steps.step4.browser_tools.execution._single_nav_state import (
        _check_error_page,
    )

    async def _run() -> tuple[bool, bool]:
        token = case_context._case_expects_denial.set(True)
        try:
            expected = await _check_error_page(_Page("403 Forbidden 无权限访问"))
        finally:
            case_context._case_expects_denial.reset(token)
        # 反向：非期望拒绝时，403 页面仍应判为错误页（防"把守卫放宽成永假"）
        default = await _check_error_page(_Page("403 Forbidden 无权限访问"))
        return expected, default

    expected, default = asyncio.run(_run())
    assert expected is False, "期望被拒绝 → 403 不得算错误页"
    assert default is True, "未期望拒绝 → 403 仍算错误页（守卫不得放宽）"


def test_no_stale_engine_attribute_read():
    """不得回退成读 engine._case_expects_denial（旧方案，且从未被写入）。"""
    for path in (NAV_STATE, BASE_MIXIN, ACTION):
        assert (
            'getattr(engine, "_case_expects_denial"' not in _src(path)
        ), f"{path.name} 仍引用已废弃的 engine 属性"


def test_negated_denial_phrase_is_not_a_denial_assertion():
    """否定语境下的拒绝词不是拒绝断言（D39 同族极性反转）。

    实测来源：UI 用例断言"被测页面已渲染出内容承载元素（**非**错误页）"，
    旧实现只做 `re.search` → 命中"错误页" → 判为"期望被拒绝" → 该用例不再把
    真实 403/404 页判为失败（假 PASS 风险）且被错误指派低权身份。
    实测口径：615 条用例中 True 由 199 修正为 145（UI 类 77→23）。
    """
    from src.pipeline.step3._identity_assign import (
        _has_affirmative_denial,
        case_expects_denial,
    )

    # 否定语境 → 不是拒绝断言
    for text in ("非错误页", "无错误提示", "不出现403", "避免权限不足", "防止越权"):
        assert _has_affirmative_denial(text) is False, f"否定语境被误判: {text}"

    # 肯定语境 → 仍是拒绝断言（守卫不得放宽）
    for text in (
        "低权身份执行受限操作被系统拒绝（403/权限不足/重定向登录）",
        "未登录用户不可访问该页面",
        "系统提示无权限访问",
        "页面弹出错误提示",
    ):
        assert _has_affirmative_denial(text) is True, f"肯定语境被漏判: {text}"

    # 端到端：断言"非错误页"的用例不得被当成期望被拒绝
    layout_case = {
        "case_type": "UI",
        "title": "页面布局对齐与内容可见",
        "checks": [
            {
                "check": {"type": "element_exists", "expected": "存在"},
                "expect": "被测页面已渲染出内容承载元素（非白屏/非错误页）",
            }
        ],
    }
    assert case_expects_denial(layout_case) is False
