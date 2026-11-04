# -*- coding: utf-8 -*-
"""契约：并行 worker 的 page 必须 task-local 隔离。

背景（D62，实测）：
  `parallel.py` 的 worker 曾用 `engine._page = _worker_page` 把页面挂到**单例 engine**。
  并发时 worker 互相覆盖 —— A 执行到一半读到的可能是 B 的 page，操作落到错误页面，
  表现为间歇性 ERROR/HARNESS_ERROR（**引擎自造的假 FAIL**），直接污染通过率。

本测试用真并发（asyncio.gather）验证隔离成立，且串行路径语义不变。
"""

from __future__ import annotations

import asyncio


class _FakeEngine:
    """复刻 LifecycleMixin 的 `_page` 动态视图行为（真实实现见 lifecycle.py）。"""

    _page_real = None

    @property
    def _page(self):
        from src.pipeline.steps.step4.browser_core._worker_page import get_worker_page

        _p = get_worker_page()
        return _p if _p is not None else self._page_real

    @_page.setter
    def _page(self, value):
        self._page_real = value


def test_worker_page_default_is_none():
    from src.pipeline.steps.step4.browser_core._worker_page import get_worker_page

    assert get_worker_page() is None


def test_no_worker_context_falls_back_to_engine_page():
    """无并行上下文（串行路径）→ 读主 page，语义与改造前一致。"""
    eng = _FakeEngine()
    eng._page = "MAIN"
    assert eng._page == "MAIN"


def test_worker_page_overrides_engine_page():
    eng = _FakeEngine()
    eng._page = "MAIN"
    from src.pipeline.steps.step4.browser_core._worker_page import set_worker_page

    async def _run():
        set_worker_page("W1")
        return eng._page

    assert asyncio.run(_run()) == "W1"
    assert eng._page == "MAIN"


def test_concurrent_workers_do_not_leak_page():
    """并发 worker 各自读到自己的 page —— 这是并行正确性的前提。"""
    from src.pipeline.steps.step4.browser_core._worker_page import set_worker_page

    eng = _FakeEngine()
    eng._page = "MAIN"
    seen = {}

    async def _worker(idx):
        set_worker_page("P%d" % idx)
        for _ in range(30):
            await asyncio.sleep(0.005)
            seen.setdefault(idx, []).append(eng._page)
        set_worker_page(None)
        return eng._page

    async def _main():
        results = await asyncio.gather(*[_worker(i) for i in range(6)])
        return results

    results = asyncio.run(_main())
    for idx, pages in seen.items():
        assert set(pages) == {"P%d" % idx}, "worker %d 读到了别人的 page: %r" % (idx, set(pages))
    assert set(results) == {"MAIN"}, "解除绑定后未回落主 page: %r" % (set(results),)
