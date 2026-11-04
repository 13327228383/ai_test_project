# -*- coding: utf-8 -*-
"""回归测试：差距3 —— 本地视觉降级链在无 VLM 时能产出真实判定（非恒 NO_DATA）。

不依赖浏览器/DB/外部模型：直接驱动 LocalBackend + registry.verify_with_fallback，
验证 expected_elements 上下文让「期望元素存在/缺失」断言产生 PASS/FAIL。
"""
from src.oracle.vlm import VisualStatus, registry
from src.oracle.vlm.backends.local import LocalBackend, ElementAsserter


def test_extract_keywords_from_quotes():
    assert ElementAsserter.extract_keywords('页面应显示「保存成功」') == ["保存成功"]
    assert ElementAsserter.extract_keywords("标题为 '已登录'") == ["已登录"]


def test_local_backend_pass_when_element_present():
    lb = LocalBackend()
    v = lb.verify(
        b"",
        '应显示「欢迎」',
        {"dom_summary": "导航栏 欢迎 首页", "expected_elements": ["欢迎"]},
    )
    assert v.is_conclusive()
    assert v.status is VisualStatus.PASS


def test_local_backend_fail_when_single_element_missing():
    lb = LocalBackend()
    v = lb.verify(
        b"",
        '应显示「导出按钮」',
        {"dom_summary": "页面无相关内容", "expected_elements": ["导出按钮"]},
    )
    assert v.status is VisualStatus.FAIL


def test_registry_fallback_with_expected_elements():
    """registry.verify_with_fallback 拿到 expected_elements 后应产出 PASS。"""
    verdict = registry.verify_with_fallback(
        b"",
        '标题包含「报表统计」',
        {"dom_summary": "报表统计 数据 图表", "expected_elements": ["报表统计"]},
    )
    assert verdict.status is VisualStatus.PASS