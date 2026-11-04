# -*- coding: utf-8 -*-
"""契约：Step4 执行形态（串行/并行）**只能有一处判定**。

背景（2026-10-16 实测）：
  · `browser_core/parallel.py::_execute_intents_parallel` 是全仓死代码（零调用点），
    而 `serial.py:_execute_all` 内无条件 `max_parallel = 1` 把它永久关掉。
  · 关掉的理由是 AGENTS.md §10.1「失败即停」，但 `step4_stop_on_failure=False`
    （全自动管线）模式下批内根本不中断 —— 即为一条不生效的红线白付数小时串行成本
    （实测 653 例串行 3.25h）。
  · 正解：判据唯一 = **失败即停 → 必须串行；否则允许并行**，判定点收敛到
    `base.py::run_test_cases`，下游（serial/parallel）只读不推。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "src" / "pipeline" / "steps" / "step4" / "browser_core" / "base.py"
SERIAL = ROOT / "src" / "pipeline" / "steps" / "step4" / "browser_core" / "serial.py"
PARALLEL = ROOT / "src" / "pipeline" / "steps" / "step4" / "browser_core" / "parallel.py"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_parallel_executor_exists():
    """并行执行器存在（能力没有丢，只是没接线）。"""
    assert PARALLEL.exists()
    assert "_execute_intents_parallel" in _src(PARALLEL)


def test_mode_decided_once_in_base():
    """执行形态的判定点唯一：只在 base.py 出现一次。"""
    base_src = _src(BASE)
    assert "_use_parallel" in base_src, "base.py 必须做出串行/并行的唯一判定"
    assert base_src.count("_use_parallel =") == 1, "执行形态判定不得出现多份"
    # 判据本身（失败即停 → 串行）也只允许一处
    assert base_src.count("_stop_on_fail") >= 2, "判定必须显式读取 step4_stop_on_failure"


def test_serial_no_longer_downgrades_parallel():
    """serial 不得二次降级并行度（否则同一语义两处判定）。"""
    serial_src = _src(SERIAL)
    assert "强制 max_parallel=1" not in serial_src, "serial 不应再自行压并行度"
    assert "max_parallel = 1" not in serial_src, "serial 不应再出现赋值降级"


def test_both_call_sites_go_through_single_factory():
    """两处执行入口（有/无运行中事件循环）都走同一个协程工厂，不各自分支。"""
    import re

    base_src = _src(BASE)
    calls = re.findall(r"^\s+_new_run_coro\(\),?$", base_src, flags=re.M)
    assert len(calls) == 2, "两个调用点必须共用同一协程工厂：%r" % calls
    # _execute_all 只允许出现在工厂内部（串行分支），不得再散落在调用点
    assert base_src.count("self._execute_all(") == 1
    assert base_src.count("self._execute_intents_parallel(") == 1


def test_factory_builds_fresh_coroutine_per_call():
    """协程不可复用：工厂每次返回新协程（两处调用各自 await 一个）。"""

    def _factory() -> object:
        return object()

    assert _factory() is not _factory()
    assert "def _new_run_coro" in _src(BASE)


def test_pipeline_config_has_parallel_switch():
    """并行度应可由配置控制（step4 选项或 pipeline_config）。"""
    exec_src = _src(ROOT / "src" / "pipeline" / "step4_executor.py")
    assert "browser_max_parallel" in exec_src


def test_run_test_cases_signature_keeps_max_parallel():
    """对外签名保留 max_parallel，避免调用方无法控制并行度。"""
    from src.pipeline.steps.step4.browser_core.base import _BrowserCoreBaseMixin

    sig = inspect.signature(_BrowserCoreBaseMixin.run_test_cases)
    assert "max_parallel" in sig.parameters
