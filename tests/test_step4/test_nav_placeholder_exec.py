# -*- coding: utf-8 -*-
"""单元测试：执行端「占位导航目标如实失败」（AGENTS.md §4.17-B）。

背景：生成端曾在无导航事实时兜底 `nav_url or nav_target or "/"`，把站点根伪装成
目标页；执行端把 "/" 当相对路径拼接 → 静默停在首页，再靠 AI 猜按钮，最终可能判
PASS（实测 ~488 次 "导航URL拼接: http://127.0.0.1:8020/"）。

契约：占位导航（无 url，target 为 ""/"/"/"/login"）不是目标页 —— 解析不到即
如实失败（``_nav_ok=False``），绝不把站点根当作"已到达目标页"。
判据唯一源：生成端 ``_navigation_resolver.is_placeholder_nav``。
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.pipeline.steps.step4.browser_structured_executor import (  # noqa: E402
    _resolve_navigate_url,
)


class _Engine:
    """占位分支不应触碰浏览器；任何访问都算违约。"""

    _page = None

    def __getattr__(self, _name):  # noqa: D105
        raise AssertionError("占位导航不得访问引擎/浏览器: %s" % _name)


def _run(action):
    return asyncio.run(_resolve_navigate_url(_Engine(), action))


def test_placeholder_nav_fails_honestly():
    for _hint in ("", "/", "/login"):
        _a = {"type": "navigate", "target_hint": _hint}
        assert _run(_a) is None, _hint
        assert _a["_nav_ok"] is False, _hint
        assert _a["url"] == "", _hint


def test_resolved_nav_is_not_treated_as_placeholder():
    """已解析出真实 URL 的导航照常返回（不误判为占位）。"""
    _a = {"type": "navigate", "url": "http://127.0.0.1:8020/products"}
    assert _run(_a) == "http://127.0.0.1:8020/products"


def test_non_navigate_action_untouched():
    _a = {"type": "click", "target_hint": "/"}
    assert _run(_a) == ""
