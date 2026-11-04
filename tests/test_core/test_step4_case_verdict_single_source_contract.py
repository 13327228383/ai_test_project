# -*- coding: utf-8 -*-
"""契约：用例终态（PASS/FAIL/SKIP）与「失败即停」判据**各只能有一处**。

背景（2026-10-16 实测，D61）：
  · 并行路径 `parallel.py` 硬编码 `if total_failed > 0: break`，与串行路径
    「读 `step4_stop_on_failure` 配置」是**两份相反的判据** —— 全自动模式下
    653 个用例只跑 3 个就 `run 结束 status=completed`，650 例被静默吞掉。
  · 触发它的直接原因是终态没有结构字段：worker 返回只有 `passed` 布尔，
    `SKIP`/`BLOCKED` 被 `else: total_failed += 1` 当成失败
    （实测首批 PASS 1 + SKIP 2 → failed=2 → 即停）。
  · 正解：终态在 `_execute_one_intent` 一次性判定并落 `status` 字段，
    分类统一走 `browser_core/case_verdict.py`；即停判据统一走
    `BrowserCore._should_stop_on_failure()`；下游只读不推。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BC = ROOT / "src" / "pipeline" / "steps" / "step4" / "browser_core"
BASE = BC / "base.py"
SERIAL = BC / "serial.py"
PARALLEL = BC / "parallel.py"
SINGLE = BC / "single_mixins" / "_base.py"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------- 终态分类 ----------


def test_case_verdict_classifies_skip_as_non_failure():
    """SKIP/BLOCKED 不是失败 —— 这是本轮 D61 的直接触发条件。"""
    from src.pipeline.steps.step4.browser_core.case_verdict import (
        count_bucket,
        is_terminal_failure,
    )

    assert count_bucket({"status": "PASS", "passed": True}) == "pass"
    assert count_bucket({"status": "FAIL", "passed": False}) == "fail"
    assert count_bucket({"status": "ERROR", "passed": False}) == "fail"
    assert count_bucket({"status": "SKIP", "passed": False}) == "skip"
    assert count_bucket({"status": "BLOCKED", "passed": False}) == "skip"
    assert count_bucket({"status": "MANUAL_REVIEW", "passed": False}) == "skip"
    # 无 status 的历史 result 按 passed 回落，不抛异常
    assert count_bucket({"passed": True}) == "pass"
    assert count_bucket({"passed": False}) == "fail"
    assert not is_terminal_failure({"status": "SKIP"})


def test_execute_one_intent_returns_status():
    """终态必须落到返回结构里，否则下游只能拿 passed 布尔重推。"""
    single_src = _src(SINGLE)
    # 返回 dict 里必须有 "status": status
    assert re.search(r'"status":\s*status', single_src), (
        "_execute_one_intent 的返回 dict 必须携带 status 字段"
    )


# ---------- 失败即停判据唯一 ----------


def test_stop_on_failure_read_once():
    """`step4_stop_on_failure` 的运行期读取只允许在 base.py 一处。"""
    hits = []
    for p in (BASE, SERIAL, PARALLEL):
        for i, line in enumerate(_src(p).splitlines(), 1):
            s = line.strip()
            if s.startswith("#") or s.startswith('"') or 'step4_stop_on_failure' not in s:
                continue
            if "getattr" in s or "_should_stop_on_failure()" in s:
                hits.append((p.name, i, s))
    assert len(hits) == 1, "失败即停判据出现多处读取：%r" % hits
    assert hits[0][0] == "base.py"


def test_serial_and_parallel_use_shared_predicate():
    """两条路径都调同一个判据方法，不各写一份。"""
    serial_src = _src(SERIAL)
    parallel_src = _src(PARALLEL)
    assert serial_src.count("self._should_stop_on_failure()") == 2
    assert parallel_src.count("self._should_stop_on_failure()") == 1
    # 并行不得再出现无条件的批次即停
    assert "if total_failed > 0:" not in parallel_src


def test_serial_and_parallel_count_by_bucket():
    """计数读 case_verdict.count_bucket，不用 passed 布尔重推终态。"""
    assert "count_bucket" in _src(SERIAL)
    assert "count_bucket" in _src(PARALLEL)
    # 并行对异常分支也要标 status=ERROR，避免被当 SKIP 漏掉
    assert '"status": "ERROR"' in _src(PARALLEL)


def test_predicate_method_exists():
    from src.pipeline.steps.step4.browser_core.base import _BrowserCoreBaseMixin

    assert hasattr(_BrowserCoreBaseMixin, "_should_stop_on_failure")
    fn = getattr(_BrowserCoreBaseMixin, "_should_stop_on_failure")
    assert callable(fn)
