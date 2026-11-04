# -*- coding: utf-8 -*-
"""单元测试：step4 视觉横切门禁（visual_checker.run_visual_gate）。

对称 a11y/perf 横切维度：
  - 配置驱动（target_cfg["visual"]），不写死靶子；
  - fail-closed：无期望 / 截图失败 / 判定链不可用 → 诚实 NO_DATA（passed=None），
    绝不伪造 PASS；
  - 判定核心复用 _verify_visual._try_vlm_fallback（视觉只看图，不依赖 DOM 文本）。

Corresponds to P3 视觉复核接入常规流水线全量门禁。
"""

import pytest

from src.pipeline.steps.step4.empirical import visual_checker as vc
from src.pipeline.steps.step4.verification import _verify_visual as _vv


class _FakePage:
    """同步 Playwright Page 的最小桩：截图返回给定 bytes。"""

    def __init__(self, screenshot_bytes: bytes = b"fake-png"):
        self._bytes = screenshot_bytes
        self.screenshot_error = False

    def screenshot(self, full_page: bool = False):
        if self.screenshot_error:
            raise RuntimeError("boom")
        return self._bytes


def _empty_results():
    """合格的空结果骨架（断言各分支字段齐全）。"""
    return {
        "enabled": None,
        "source": "step4",
        "status": None,
        "passed": None,
        "skipped": None,
        "confidence": None,
        "reason": None,
        "evidence": None,
        "error": None,
    }


def test_disabled_returns_empty():
    r = vc.run_visual_gate(_FakePage(), {"visual": {"enabled": False}})
    assert r["enabled"] is False
    assert r["status"] == ""
    assert r["passed"] is None


def test_no_expectation_is_honest_no_data():
    # 视觉无法无期望硬判 → 诚实 NO_DATA，绝不假 PASS
    r = vc.run_visual_gate(_FakePage(), {})
    assert r["enabled"] is True
    assert r["status"] == _vv.STATUS_NO_DATA
    assert r["passed"] is None
    assert r["skipped"] is True
    assert r["reason"] == _vv.REASON_NO_EXPECTATION


def test_none_page_is_no_data():
    r = vc.run_visual_gate(None, {"visual": {"expectation": "页面渲染正常"}})
    assert r["status"] == _vv.STATUS_NO_DATA
    assert r["passed"] is None
    assert r["reason"] == _vv.REASON_NO_PAGE


def test_screenshot_failure_is_no_data():
    page = _FakePage()
    page.screenshot_error = True
    r = vc.run_visual_gate(page, {"visual": {"expectation": "页面渲染正常"}})
    assert r["status"] == _vv.STATUS_NO_DATA
    assert r["passed"] is None
    assert r["error"]


def test_passthrough_of_pass_result(monkeypatch):
    async def _fake_fallback(screenshot_bytes, expectation, page):
        return _vv._verdict(True, "视觉断言[页面渲染正常]: 符合", 0.95)

    monkeypatch.setattr(_vv, "_try_vlm_fallback", _fake_fallback)
    r = vc.run_visual_gate(
        _FakePage(), {"visual": {"expectation": "页面渲染正常"}}
    )
    assert r["enabled"] is True
    assert r["status"] == _vv.STATUS_PASS
    assert r["passed"] is True
    assert r["skipped"] is False
    assert r["confidence"] == pytest.approx(0.95)


def test_passthrough_of_fail_result(monkeypatch):
    async def _fake_fallback(screenshot_bytes, expectation, page):
        return _vv._verdict(False, "视觉断言[页面渲染正常]: 不符合", 0.8)

    monkeypatch.setattr(_vv, "_try_vlm_fallback", _fake_fallback)
    r = vc.run_visual_gate(
        _FakePage(), {"visual": {"expectation": "页面渲染正常"}}
    )
    assert r["status"] == _vv.STATUS_FAIL
    assert r["passed"] is False
    assert r["skipped"] is False


def test_degraded_chain_is_no_data(monkeypatch):
    async def _fake_fallback(screenshot_bytes, expectation, page):
        return None  # 降级链全部不可用 → 门禁诚实 NO_DATA

    monkeypatch.setattr(_vv, "_try_vlm_fallback", _fake_fallback)
    r = vc.run_visual_gate(
        _FakePage(), {"visual": {"expectation": "页面渲染正常"}}
    )
    assert r["status"] == _vv.STATUS_NO_DATA
    assert r["passed"] is None