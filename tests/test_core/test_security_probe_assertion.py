# -*- coding: utf-8 -*-
"""契约：安全探针用例的断言必须观察「该类攻击的可利用痕迹」，而不是「页面能否打开」。

缺陷形状
--------
安全探针（SQLi/XSS）的期望是"注入被正确防御"，存量断言却是
`element_exists(body) + no_error` —— 只证明页面加载且无 JS 异常，对注入类攻击**不可证伪**
（假 PASS 面：实测 43 条弱断言属此类）。

契约（本测试锁定）
------------------
1. 攻击类别只由载荷库判定（`case_rules.base.classify_security_payload`，唯一事实源）；
   生成端、落库前派生、执行端取证共用同一判据。
2. 落库前唯一咽喉 `_check_guard.align_requirement_observable_assertions` 把弱断言
   升级为 `security_probe_check(attack=..., payloads=[...])`，并保留 `no_error`
   （崩溃是另一条独立防御面）。
3. 执行端 `_handle_security_probe_check` 判据 = 该类攻击痕迹出现与否：
   - sqli → 数据库错误签名（复用 `SecurityScanner.SQL_INJECTION_PATTERNS`）
   - xss  → 载荷被原样反射为活动标记（未转义反射 / `SecurityScanner.XSS_PATTERNS`）
   采集不到 HTML → N/A(SKIP)，不记 PASS（防假绿）也不记 FAIL（防假 Bug）。
"""

from __future__ import annotations

import asyncio

import pytest

from src.pipeline.step3 import _check_guard as cg
from src.pipeline.step3.case_rules.base import (
    SQL_INJECTION_PAYLOADS,
    XSS_PAYLOADS,
    classify_security_payload,
)


class _FakePage:
    """最小 page 协议：仅提供 content()/url（执行器只读这两项）。"""

    def __init__(self, html: str, url: str = "/products") -> None:
        self._html = html
        self.url = url

    async def content(self) -> str:
        return self._html


def _weak_security_case(payload: str, expect: str) -> dict:
    return {
        "case_id": "TC-REQ-001-I04",
        "req_id": "REQ-001",
        "module": "搜索",
        "scenario_type": "exploratory",
        "expected_result": expect,
        "actions": [
            {"type": "navigate", "target_hint": "/"},
            {"type": "fill", "target_hint": "搜索框", "value": payload},
            {"type": "click", "target_hint": "搜索"},
        ],
        "checks": [
            {
                "track": "ui",
                "check": {"type": "element_exists", "selector": "body"},
                "expect": "存在",
            },
            {"track": "ui", "check": {"type": "no_error", "mode": "defense"}},
        ],
    }


# ── 生成端：载荷类别 → 断言族 ────────────────────────────────────────────
def test_payload_classifier_is_single_source():
    """攻击类别判据来自载荷库本身；非载荷值一律空（不臆造类别）。"""
    assert classify_security_payload(XSS_PAYLOADS[1]) == "xss"
    assert classify_security_payload(SQL_INJECTION_PAYLOADS[0]) == "sqli"
    assert classify_security_payload("普通商品名") == ""
    assert classify_security_payload("") == ""


@pytest.mark.parametrize(
    "payload,attack",
    [(SQL_INJECTION_PAYLOADS[1], "sqli"), (XSS_PAYLOADS[0], "xss")],
)
def test_weak_security_probe_upgraded_to_probe_check(
    monkeypatch: pytest.MonkeyPatch, payload: str, attack: str
):
    monkeypatch.setattr(cg, "requirement_texts_of", lambda _rid: [])
    monkeypatch.setattr(cg, "module_requirement_texts", lambda _m: [])
    _case = _weak_security_case(payload, "系统不崩溃，注入被正确防御")

    assert cg.align_requirement_observable_assertions(_case) is True
    _types = [cg._check_type_of(c) for c in _case["checks"]]
    assert "security_probe_check" in _types, _types
    assert "no_error" in _types, "崩溃是独立防御面，no_error 必须保留"
    _probe = next(
        c for c in _case["checks"] if cg._check_type_of(c) == "security_probe_check"
    )
    _inner = _probe.get("check") or {}
    assert _inner.get("attack") == attack
    assert payload in (_inner.get("payloads") or [])


def test_non_security_weak_case_not_claimed_by_security_family(
    monkeypatch: pytest.MonkeyPatch,
):
    """普通输入值的弱断言用例不得被安全族认领（判据只看载荷库，不看期望措辞）。"""
    monkeypatch.setattr(cg, "requirement_texts_of", lambda _rid: [])
    monkeypatch.setattr(cg, "module_requirement_texts", lambda _m: [])
    _case = _weak_security_case("普通商品名", "系统不崩溃")
    cg.align_requirement_observable_assertions(_case)
    _types = [cg._check_type_of(c) for c in _case["checks"]]
    assert "security_probe_check" not in _types, _types


def test_strong_security_case_is_untouched(monkeypatch: pytest.MonkeyPatch):
    """已带强断言的用例不被改写（本判据只作用于全弱断言用例）。"""
    monkeypatch.setattr(cg, "requirement_texts_of", lambda _rid: [])
    monkeypatch.setattr(cg, "module_requirement_texts", lambda _m: [])
    _case = _weak_security_case(XSS_PAYLOADS[0], "XSS 被正确转义")
    _case["checks"] = [{"track": "ui", "check": {"type": "text_contains_check"}}]
    assert cg.align_requirement_observable_assertions(_case) is False


# ── 执行端：攻击痕迹取证 ────────────────────────────────────────────────
def _run(check: dict, html: str) -> dict:
    from src.pipeline.steps.step4.verification import _db_ui

    return asyncio.run(
        _db_ui._handle_security_probe_check(
            _FakePage(html), check, check, "security_probe_check", "exact"
        )
    )


def test_xss_escaped_reflection_passes():
    """转义后的载荷（文本节点序列化为 &lt;script&gt;）→ 防御成立。"""
    _html = "<html><body><p>&lt;script&gt;alert(1)&lt;/script&gt;</p></body></html>"
    res = _run({"attack": "xss", "payloads": [XSS_PAYLOADS[0]]}, _html)
    assert res["passed"] is True, res


def test_xss_raw_reflection_fails():
    """载荷以未转义原文留在 DOM → 反射型 XSS，必须判失败。"""
    _html = "<html><body><p>%s</p></body></html>" % XSS_PAYLOADS[0]
    res = _run({"attack": "xss", "payloads": [XSS_PAYLOADS[0]]}, _html)
    assert res["passed"] is False, res


def test_sqli_db_error_leak_fails():
    """页面泄露数据库错误签名 → 注入未被正确防御提示/处理。"""
    _html = "<html><body>You have an error in your SQL syntax; check the manual that " \
            "corresponds to your MySQL server version</body></html>"
    res = _run({"attack": "sqli", "payloads": [SQL_INJECTION_PAYLOADS[0]]}, _html)
    assert res["passed"] is False, res


def test_sqli_clean_page_passes():
    """正常页面（无 DB 错误签名）→ 防御成立。"""
    res = _run(
        {"attack": "sqli", "payloads": [SQL_INJECTION_PAYLOADS[0]]},
        "<html><body><table><tr><td>iPhone</td></tr></table></body></html>",
    )
    assert res["passed"] is True, res


def test_empty_html_is_not_applicable():
    """采集不到 HTML → N/A(SKIP)：既不是 PASS 也不是 FAIL。"""
    res = _run({"attack": "xss", "payloads": [XSS_PAYLOADS[0]]}, "")
    assert res["status"] == "SKIP" and res["skipped"] is True, res
    assert res["reason"] == "ASSERTION_NOT_APPLICABLE"


def test_missing_declaration_is_not_applicable():
    """无攻击类别/载荷声明 → N/A(SKIP)，不得凭空白判 PASS。"""
    res = _run({}, "<html><body>ok</body></html>")
    assert res["status"] == "SKIP" and res["skipped"] is True, res


def test_probe_check_registered_in_ui_dispatch():
    """执行注册表必须认识该类型，否则生成端发了、执行端不认（词表漂移）。"""
    from src.pipeline.steps.step4.verification._db_ui import _UI_CHECK_DISPATCH
    from src.pipeline.steps.step4.verification._track_protocol import (
        registered_check_types,
    )

    assert "security_probe_check" in _UI_CHECK_DISPATCH
    assert "security_probe_check" in (registered_check_types().get("ui") or frozenset())
