# -*- coding: utf-8 -*-
"""「每个操作步骤都必须携带断言」门禁。

纪律
----
用例评审页渲染"操作步骤（含逐步预期）"，但当步骤自身没有 expected 时，页面只能把
整体预期挂到最后一步 —— 即"只在末尾统一写整体预期"，这是被明令禁止的写法，也让
执行端无法逐步判定。故该不变量必须由**单一实现**保证（`step_assertions`），并在
生成端定稿点（`_check_guard.sanitize_case_checks`）无条件应用。

本门禁钉死两件事：
  1. 派生器对**每类操作动作**恒返回非空断言（不因新增动作类型退回"无断言"）；
  2. 定稿点确实调用了该单一实现（防下游另起一套 / 静默绕过）。
"""
from __future__ import annotations

import io
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_GUARD = "src/pipeline/step3/_check_guard.py"
_MOD = "src/pipeline/utils/step_assertions.py"

_OPERATIONAL_TYPES = (
    "navigate", "goto", "click", "double_click", "hover", "right_click",
    "fill", "type_text", "select_option", "check", "uncheck", "clear",
    "press_key", "upload_file", "set_date", "scroll", "wait", "verify",
    "any_future_unknown_action",
)


def _read(rel: str) -> str:
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_step_assertion_module_exists():
    assert os.path.exists(os.path.join(ROOT, _MOD)), (
        "步骤级预期的单一实现必须存在：src/pipeline/utils/step_assertions.py"
    )


@pytest.mark.parametrize("action_type", _OPERATIONAL_TYPES)
def test_every_action_type_yields_nonempty_expectation(action_type):
    """每种操作动作都必须能派生出一条非空断言（含未登记的未知动作兜底）。"""
    from src.pipeline.utils.step_assertions import derive_step_expectation

    d = derive_step_expectation({"type": action_type, "target_hint": "字段", "value": "v"})
    assert isinstance(d, dict)
    assert str(d.get("expected") or "").strip(), (
        "动作 %r 未派生出步骤级断言" % action_type
    )


def test_ensure_step_assertions_attaches_to_every_step():
    """补齐后每个操作步骤都必须携带非空 expected（取证步骤不计入操作步骤）。"""
    from src.pipeline.utils.step_assertions import ensure_step_assertions

    case = {
        "test_steps": [
            {"type": "navigate", "target_hint": "/assets/"},
            {"type": "click", "target_hint": "新增"},
            {"type": "fill", "target_hint": "名称", "value": "x"},
            {"type": "click", "target_hint": "保存"},
            {"type": "screenshot", "target_hint": "取证"},
        ]
    }
    ensure_step_assertions(case, step_key="test_steps")
    steps = case["test_steps"]
    assert all(str(s.get("expected") or "").strip() for s in steps), (
        "存在无断言的操作步骤：%r" % steps
    )
    assert all(s["type"] != "screenshot" for s in steps), (
        "纯取证步骤不是操作步骤，不应出现在步骤表"
    )


def test_guard_applies_step_assertions_at_the_single_finalize_point():
    """定稿守卫必须调用单一实现，不得在别处各写一套逐步预期。"""
    guard = _read(_GUARD)
    assert "ensure_step_assertions" in guard, (
        "定稿守卫 sanitize_case_checks 必须应用步骤级断言"
    )
    assert "from src.pipeline.utils.step_assertions import" in guard, (
        "必须 import 单一实现，禁止复制派生逻辑"
    )
