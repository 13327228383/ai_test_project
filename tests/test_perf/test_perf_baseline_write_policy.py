# -*- coding: utf-8 -*-
"""契约：基线回写策略只有一个判定入口（write_after_gate）。

同一语义（何时回写基线）此前在两条链路各写一遍：靶子链路
``perf_checker.evaluate_perf_gate`` 条件回写、流水线 step5
``_perf_gate_report.run_perf_gate`` 无条件回写 —— 后者让参照系随每次运行漂移，
与本文件声明的「已建立默认只判定不更新」自相矛盾。
现策略归位 ``perf_baseline.baseline.write_after_gate``，两条链路只调用不重推。
"""

from __future__ import annotations

import pytest

from src.extensions.perf_baseline.baseline import BaselineManager, write_after_gate
from src.extensions.perf_baseline.schemas import KIND_WEB, PerfSample


def _samples(**metrics) -> list[PerfSample]:
    return [
        PerfSample(
            kind=KIND_WEB,
            key="web::/items",
            target="demo",
            metrics=dict(metrics) or {"load_ms": 100.0},
        )
    ]


@pytest.fixture()
def manager(tmp_path) -> BaselineManager:
    return BaselineManager("demo", storage_dir=str(tmp_path))


def test_first_run_establishes_baseline(manager):
    """首轮（version==0）自动建立基线，并如实报告 before/after。"""
    cal = write_after_gate(manager, _samples(load_ms=100.0))
    assert cal["status"] == "updated"
    assert cal["before"] == 0
    assert cal["after"] == 1
    assert manager.version() == 1


def test_regular_run_does_not_drift_baseline(manager):
    """已建立基线 → 常规 run 只判定不回写（参照系稳定）。"""
    write_after_gate(manager, _samples(load_ms=100.0))
    cal = write_after_gate(manager, _samples(load_ms=900.0))
    assert cal["status"] == "skip_regular"
    assert cal["mode"] is False
    assert manager.version() == 1
    assert manager.get_entry("web::/items")["metrics"]["load_ms"]["mean"] == 100.0


def test_calibration_mode_writes_baseline(manager):
    """校准模式（update_baseline=true）才允许回写，且版本递增。"""
    write_after_gate(manager, _samples(load_ms=100.0))
    cal = write_after_gate(manager, _samples(load_ms=120.0), {"update_baseline": True})
    assert cal["status"] == "updated"
    assert cal["mode"] is True
    assert cal["before"] == 1
    assert cal["after"] == 2


def test_calibration_without_valid_samples_is_honest_skip(manager):
    """校准模式但无有效样本 → 明确 skipped_no_samples，基线不被污染。"""
    write_after_gate(manager, _samples(load_ms=100.0))
    cal = write_after_gate(manager, [], {"update_baseline": True})
    assert cal["status"] == "skipped_no_samples"
    assert manager.version() == 1


def test_invalid_samples_never_pollute_baseline(manager):
    """无效样本（无 key/无指标）不参与聚合。"""
    cal = write_after_gate(manager, [PerfSample(kind=KIND_WEB, key="", metrics={})])
    assert cal["status"] == "skipped_no_samples"
    assert manager.version() == 0
