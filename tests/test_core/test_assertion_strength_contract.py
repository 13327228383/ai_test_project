# -*- coding: utf-8 -*-
"""契约：生成端不得声明强于需求原文的期望。

缺陷形状
--------
异常/边界模板的 ``expected`` 是引擎**自撰**文案，与需求无关。只读需求（"展示全量
列表"）因此被套上"显示错误提示或跳转404"，执行端逐分支取证必然全不成立
→ 结构性假 FAIL（实测 REQ-002/007/024/038/061-I02）。

契约（本测试锁定）
------------------
1. 判据只有**一个入口**：``_check_guard.overstated_expectation_reason``（落库前咽喉，
   覆盖全部生成器含 AI 生成）。用例主张"须出现错误/拒绝反馈"而需求原文未声明该极性
   → 不落库；需求原文声明拒绝语义 → 保留。
2. 判据复用既有 SSOT（``expects_error_feedback`` / ``expects_noop_invariant`` /
   ``_REQ_TEXT_KEYS``），不另立词表；需求原文不可得时 fail-open（不删用例）。
3. 数字型唯一占位值必须只含数字（hex 后缀含 a-f 会造出非法手机号 → SUT 正确拒绝 → 假 FAIL）。
"""

from __future__ import annotations

import pytest

from src.pipeline.step3 import _check_guard as cg


def _case(expect: str, req_id: str = "REQ-999") -> dict:
    return {
        "case_id": "TC-REQ-999-I02",
        "req_id": req_id,
        "module": "商品",
        "scenario_type": "error_path",
        "expected_result": expect,
        "checks": [{"check": {"type": "element_exists", "expected": "存在"}}],
    }


def test_readonly_requirement_is_dropped(monkeypatch: pytest.MonkeyPatch):
    """只读需求未声明错误语义 → 不得落库"必须出现错误提示"的用例。"""
    monkeypatch.setattr(cg, "requirement_texts_of", lambda _rid: ["展示全量列表"])
    for _expect in ("显示错误提示或跳转404", "系统应提示校验错误"):
        _why = cg.overstated_expectation_reason(_case(_expect))
        assert _why, f"需求未声明错误极性却未拦截: {_expect!r}"


def test_negative_requirement_is_kept(monkeypatch: pytest.MonkeyPatch):
    """需求原文声明拒绝/错误语义 → 用例必须保留（门控不得放宽）。"""
    monkeypatch.setattr(
        cg, "requirement_texts_of", lambda _rid: ["删除不存在的商品返回错误提示"]
    )
    assert cg.overstated_expectation_reason(_case("显示错误提示")) == ""


def test_noop_invariant_requirement_is_kept(monkeypatch: pytest.MonkeyPatch):
    """需求声明"操作不得生效"（不变量语义）同样构成拒绝极性 → 保留。"""
    monkeypatch.setattr(
        cg,
        "requirement_texts_of",
        lambda _rid: ["维护中的商品不允许删除"],
    )
    assert cg.overstated_expectation_reason(_case("显示校验提示")) == ""


def test_positive_expectation_is_never_dropped(monkeypatch: pytest.MonkeyPatch):
    """正向期望（无错误极性）不受门控影响。"""
    monkeypatch.setattr(cg, "requirement_texts_of", lambda _rid: ["展示全量列表"])
    assert cg.overstated_expectation_reason(_case("列表正常展示，无报错")) == ""


def test_boundary_scenario_is_exempt(monkeypatch: pytest.MonkeyPatch):
    """边界/容错模板允许"正确处理"分支成立，不构成超强主张 → 不受门控。"""
    monkeypatch.setattr(cg, "requirement_texts_of", lambda _rid: ["展示全量列表"])
    _c = _case("显示错误提示或正确处理")
    _c["scenario_type"] = "boundary"
    assert cg.overstated_expectation_reason(_c) == ""


def test_fail_open_when_requirement_text_missing(monkeypatch: pytest.MonkeyPatch):
    """需求原文不可得 → fail-open（无判据不删用例）。"""
    monkeypatch.setattr(cg, "requirement_texts_of", lambda _rid: [])
    monkeypatch.setattr(cg, "module_requirement_texts", lambda _m: [])
    assert cg.overstated_expectation_reason(_case("显示错误提示")) == ""


def test_numeric_unique_value_is_digits_only():
    """数字型唯一占位值只含数字（否则手机号等字段被 SUT 正确拒绝 → 假 FAIL）。"""
    from src.pipeline.step3._fallback_cases import _gen_unique_value

    _v = _gen_unique_value({"req_id": "REQ-069"}, "phone")
    assert _v.isdigit(), f"数字型唯一值含非数字字符: {_v!r}"
    assert len(_v) == 11, f"手机号形态应为 11 位: {_v!r}"
    assert _v.startswith("138")
