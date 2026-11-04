# -*- coding: utf-8 -*-
"""D40 契约：安全用例必须断言「防御生效」，不得断言「防护实现形态存在」。

根因（实测 fullcov_sut v9，9 条 -I06 假 FAIL）：
    CSRF 用例断言 `element_exists(CSRF token 选择器)` —— 主张"表单**必须**含
    CSRF token"。该主张**不是需求派生的**（需求集 58 条 0 条提 CSRF），
    且 fullcov_sut 模板/蓝图 0 处 csrf。实测全部 9 条 -I06 **只**挂在该断言上，
    而承载真实需求的 semantic_check 一律 ✓ → 单条用例内部自相矛盾。

为什么是引擎缺陷而非靶子问题：
    CSRF 缺席这一**安全事实**已由 `security_scanner._check_csrf` 以
    SecurityFinding(high, CSRF-001) 正确捕获并进入 security 报告；
    再按"存在性"硬断言 = 把同一安全观察**重复计分**到功能通过率，
    且用的是需求从未提出的义务。

正确形态（与同函数内 S01 SQL 注入用例 2026-09-28 的同型修复对齐）：
    安全用例断言 **防御生效的可观察证据** —— 页面主体仍渲染 + 无致命错误。
"""
from __future__ import annotations

import inspect
import re

from src.pipeline.step3.case_rules import base as B


def _security_csrf_source() -> str:
    """取生成安全用例的方法源码（`_build_security_cases`）。"""
    for name in dir(B):
        obj = getattr(B, name)
        cls = getattr(obj, "__dict__", {})
        fn = cls.get("_build_security_cases")
        if fn is not None and inspect.isfunction(fn):
            return inspect.getsource(fn)
    raise AssertionError("未找到 _build_security_cases 方法")


def test_csrf_case_does_not_assert_token_presence():
    """CSRF 用例不得再以 CSRF token 选择器作 element_exists 断言。"""
    src = _security_csrf_source()
    assert "_CSRF_SELECTOR_CSV" not in src, (
        "CSRF 用例仍以 token 选择器作存在性断言 —— 对无 CSRF 的目标系统"
        "必然假 FAIL（需求未要求的能力被计分）。"
    )
    assert "CSRF Token 存在于表单中" not in src, (
        "CSRF 用例仍主张『token 存在于表单中』 —— 断言方向错误。"
    )


def test_csrf_case_asserts_defence_effect():
    """CSRF 用例应断言防御生效证据（body 渲染 + no_error）。"""
    src = _security_csrf_source()
    assert '"body"' in src, "CSRF 用例缺少『页面主体仍渲染』断言"
    assert "no_error" in src, "CSRF 用例缺少 no_error 断言"
    assert "未能因缺失防护而崩溃" in src or "未因缺失防护而崩溃" in src, (
        "CSRF 用例未表达『防御生效（不崩溃/无泄露）』的命题"
    )


def test_csrf_module_no_longer_imports_selector_csv():
    """模块不得保留已失效的 CSRF 选择器 import（死引用）。"""
    src = inspect.getsource(B)
    assert "CSRF_TOKEN_SELECTOR_CSV as _CSRF_SELECTOR_CSV" not in src, (
        "base.py 仍 import 未使用的 CSRF 选择器 —— 死引用。"
    )


def test_csrf_finding_still_reported_by_scanner():
    """CSRF 缺失仍须由安全扫描器以 finding 上报（不得因改断言而丢失报告）。"""
    from src.extensions import security_scanner as SS
    src = inspect.getsource(SS)
    assert "CSRF-001" in src, "安全扫描器丢失 CSRF finding 规则"
    assert "_check_csrf" in src, "安全扫描器丢失 CSRF 检测入口"
