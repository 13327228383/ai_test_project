# -*- coding: utf-8 -*-
"""速度契约：导航后的就绪等待必须是**条件驱动**，不得是固定 sleep。

背景（缺陷形状）
----------------
`engine.goto` 是全部用例共用的唯一导航原语。它内部曾无条件
``await asyncio.sleep(3)``（外加最多 15×1s 轮询），刷新重试路径更是 ``sleep(3)+sleep(2)``。
本地靶子 100ms 内即可交互，却每次导航白等 3s —— 一次全量 run 约 1000 次导航，
单这一处白烧 ≥50 分钟，是"几百条用例跑几十小时"的主因之一。

契约
----
1. 就绪判定只有一个实现：``src.core.page_ready.wait_page_ready``。
2. `goto` 不得再出现无条件定长 sleep。
3. 导航热路径中 ``engine.goto(...)`` 之后不得紧跟定长 ``asyncio.sleep``。
"""

from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = ROOT / "src" / "pipeline" / "steps" / "step4" / "playwright_engine" / "lifecycle.py"
MENU = (ROOT / "src" / "pipeline" / "steps" / "step4" / "browser_tools"
        / "execution" / "_single_menu.py")
NAV_HELPERS = (ROOT / "src" / "pipeline" / "steps" / "step4" / "browser_tools"
               / "execution" / "_single_nav_helpers.py")


def _src(p: Path) -> str:
    assert p.exists(), f"缺失文件: {p}"
    return p.read_text(encoding="utf-8")


def _goto_body() -> str:
    """截取 `async def goto` 的函数体源码。"""
    src = _src(LIFECYCLE)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "goto":
            return ast.get_source_segment(src, node) or ""
    raise AssertionError("未找到 goto 定义")


def test_goto_has_no_unconditional_fixed_sleep():
    """goto 内不得留下定长 sleep（≥1s）——那正是 50 分钟白等的来源。"""
    body = _goto_body()
    assert "wait_page_ready" in body, "goto 必须走条件就绪等待"
    offenders = []
    for node in ast.walk(ast.parse(body.strip())):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "attr", "") or getattr(fn, "id", "")
        if name not in ("sleep",):
            continue
        for a in node.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, (int, float)):
                if float(a.value) >= 1.0:
                    offenders.append(float(a.value))
    assert not offenders, f"goto 仍有无条件定长 sleep: {offenders}"


def test_no_fixed_sleep_after_goto_in_hot_path():
    """导航热路径中 goto 之后不得紧跟定长 asyncio.sleep。"""
    for path in (MENU, NAV_HELPERS):
        src = _src(path)
        lines = src.splitlines()
        hits = []
        for i, line in enumerate(lines):
            if "engine.goto(" not in line:
                continue
            # 看后续 2 行
            for nxt in lines[i + 1 : i + 3]:
                s = nxt.strip()
                if s.startswith("await asyncio.sleep(") and any(
                    c.isdigit() for c in s
                ):
                    hits.append(f"{path.name}:{i + 1} → {s[:50]}")
        assert not hits, f"goto 后仍有定长 sleep: {hits}"


def test_wait_page_ready_is_condition_driven_and_bounded():
    """热页 ~一个轮询周期即返回；不可交互页不在上限内提前返回。"""
    from src.core.page_ready import READY_POLL_MS, wait_page_ready

    class _ReadyPage:
        def __init__(self, ready: bool) -> None:
            self._ready = ready

        async def evaluate(self, _js: str) -> bool:
            return self._ready

    async def _timed(ready: bool) -> tuple[bool, float]:
        t0 = time.monotonic()
        r = await wait_page_ready(_ReadyPage(ready), cap_ms=300)
        return r, (time.monotonic() - t0) * 1000

    ok, ms = asyncio.run(_timed(True))
    assert ok is True
    assert ms < READY_POLL_MS * 3, f"就绪页应立即返回，实测 {ms:.0f}ms"

    bad, bad_ms = asyncio.run(_timed(False))
    assert bad is False
    assert bad_ms >= 300, f"未就绪页必须耗尽上限，实测 {bad_ms:.0f}ms"


def test_ready_probe_failure_is_fail_open():
    """探测异常必须 fail-open 放行，不得把等待异常升级成用例失败。"""
    from src.core.page_ready import wait_page_ready

    class _BrokenPage:
        async def evaluate(self, _js: str) -> bool:
            raise RuntimeError("CDP 断开")

    assert asyncio.run(wait_page_ready(_BrokenPage(), cap_ms=200)) is False
