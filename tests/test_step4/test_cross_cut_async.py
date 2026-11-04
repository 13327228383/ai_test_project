# -*- coding: utf-8 -*-
"""回归测试：横切门禁 async 统一入口（常规流水线/串行后端挂载用）。

背景（2026-09-02）：a11y/perf/visual 三横切门禁原先只挂 target_empirical 同步
后端；常规流水线走 async PlaywrightEngine，本测试验证 ``run_cross_cut_gates_async``
能一次抓 html+screenshot、三入门禁 fail-safe 输出与同步入口同构的结果。
"""
import asyncio

import pytest

from src.pipeline.steps.step4.empirical.cross_cut_gates import (
    run_cross_cut_gates_async,
    _StaticPage,
    _fetch_page_snapshot,
    summarize_cross_cut,
)


class _FakeAsyncPage:
    """最小 async Page 替身：content / screenshot 均为协程。"""

    url = "http://fake.local/"

    async def content(self):
        return '<html><body><img src="hero.png"></body></html>'

    async def screenshot(self, full_page=False):
        return b"FAKEPNG"


class _BrokenAsyncPage(_FakeAsyncPage):
    """content 抛异常（验证 a11y 诚实失败，不伪造结论）。"""

    async def content(self):
        raise RuntimeError("boom")


def test_async_dispatcher_produces_cross_cut_dimensions():
    res = asyncio.run(
        run_cross_cut_gates_async(
            _FakeAsyncPage(), [], {"target_key": "fake", "url": "http://fake.local/"},
            run_id="r1", case_id="c1",
        )
    )
    # ★ 根因修复(2026-09-21)：断言不要硬编码「恰好等于某固定维度集」，否则引擎
    #   每新增一个合法横切维度(2026-09-05 security、2026-09-16 usability)该断言
    #   就会白报 FAIL。改为断言「权威横切维度集 ⊆ 实际产出」（见 cross_cut_gates
    #   run_cross_cut_gates_async 的维度清单），对未来维度扩展保持稳健。
    expected_dims = {"a11y", "perf", "visual", "security", "usability"}
    assert expected_dims <= set(res.keys())
    # a11y：img 无 alt → 有发现
    assert res["a11y"]["enabled"] is True
    assert res["a11y"]["total"] >= 1
    # visual：未配置 expectation → 诚实 NO_DATA，不伪造 PASS
    assert res["visual"]["status"] == "NO_DATA"
    assert res["visual"]["passed"] is None
    # perf：无有效样本(页面无 evaluate) → fail-safe，verdicts 存在
    assert res["perf"]["enabled"] is True


def test_async_dispatcher_fail_open_on_broken_page():
    res = asyncio.run(
        run_cross_cut_gates_async(
            _BrokenAsyncPage(), [],
            {"target_key": "fake", "url": "http://fake.local/"},
            run_id="r1",
        )
    )
    # a11y 抓 HTML 失败 → error 非空（诚实 fail-closed），不抛异常
    assert res["a11y"]["enabled"] is True
    assert "error" in res["a11y"] and res["a11y"]["error"]
    assert res["visual"]["status"] in (None, "", "NO_DATA")
    assert summarize_cross_cut(res)


def test_static_page_shim_serves_html_and_screenshot():
    sp = _StaticPage(html="<html/>", screenshot=b"SHOT")
    assert sp.content() == "<html/>"
    assert sp.screenshot() == b"SHOT"
    assert sp.url == ""
    # 无截图的 shim → screenshot 必须抛（视觉门禁据此诚实判 NO_DATA）
    sp2 = _StaticPage(html="<html/>")
    with pytest.raises(RuntimeError):
        sp2.screenshot()


def test_fetch_page_snapshot_returns_awaitables_resolved():
    html, shot = asyncio.run(_fetch_page_snapshot(_FakeAsyncPage()))
    assert html == '<html><body><img src="hero.png"></body></html>'
    assert shot == b"FAKEPNG"


def test_async_dispatcher_feeds_screenshot_to_visual_gate(monkeypatch):
    """关键验证：async 抓取的截图 bytes 必须真正喂给 visual 门禁的 VLM 判定。

    配置 expectation 后 visual_checker 不再短路，会调用 page.screenshot() →
    _StaticPage 返回抓到的 bytes → _run_vlm_off_loop 把 bytes 交给 _try_vlm_fallback。
    这里 monkeypatch 捕获收到的 bytes，证明快照"抓取→喂给检定器"链路是通的。
    """
    from src.pipeline.steps.step4.verification import _verify_visual as _vv

    captured = {}

    async def _fake_fallback(screenshot_bytes, expectation, page):
        captured["bytes"] = screenshot_bytes
        captured["expectation"] = expectation
        return _vv._verdict(True, "视觉断言[页面渲染正常]: 符合", 0.95)

    monkeypatch.setattr(_vv, "_try_vlm_fallback", _fake_fallback)

    res = asyncio.run(
        run_cross_cut_gates_async(
            _FakeAsyncPage(), [],
            {"target_key": "fake", "url": "http://fake.local/",
             "visual": {"expectation": "页面渲染正常"}},
            run_id="r1",
        )
    )
    # 截图 bytes 到达 VLM 判定
    assert captured.get("bytes") == b"FAKEPNG"
    assert captured.get("expectation") == "页面渲染正常"
    # 视觉门禁透传了 VLM 的 PASS 终态
    assert res["visual"]["status"] == _vv.STATUS_PASS
    assert res["visual"]["passed"] is True
    assert res["visual"]["confidence"] == pytest.approx(0.95)