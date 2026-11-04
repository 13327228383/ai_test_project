# -*- coding: utf-8 -*-
"""性能·基线层 + 集成层(report/regression) 测试：跨运行回比与退化预警。"""
from __future__ import annotations

import pytest

from src.extensions.perf_baseline.baseline import BaselineManager
from src.extensions.perf_baseline.report import build_perf_report
from src.extensions.perf_baseline.regression import compare, compare_with_history, save_regression_report
from src.extensions.perf_baseline.schemas import (
    GATE_PASS,
    KIND_API,
    PerfSample,
    GateVerdict,
)


def _sample(key: str = "api::GET /items", elapsed: float = 100.0,
            target: str = "demo_t") -> PerfSample:
    return PerfSample(kind=KIND_API, key=key, target=target,
                      metrics={"elapsed_ms": elapsed, "status_code": 200})


# ── 基线层：聚合、版本、持久化、无效样本不污染 ─────────────────────────
def test_baseline_update_and_version(tmp_path):
    bm = BaselineManager("demo_t", str(tmp_path))
    assert bm.version() == 0 and bm.get_entry("api::GET /items") is None
    bm.update([_sample(elapsed=100), _sample(elapsed=120)], run_id="r1")
    entry = bm.get_entry("api::GET /items")
    assert entry is not None and entry["samples"] == 2
    assert bm.version() == 1 and bm.entries()["api::GET /items"]["kind"] == KIND_API
    # 持久化：重开读取，基线仍在
    bm2 = BaselineManager("demo_t", str(tmp_path))
    assert bm2.version() == 1 and bm2.has_baseline("api::GET /items")


def test_baseline_ignores_invalid_samples(tmp_path):
    bm = BaselineManager("demo_t", str(tmp_path))
    bad = PerfSample(kind=KIND_API, key="k", target="demo_t", error="boom")
    bm.update([bad])
    assert bm.version() == 0 and bm.get_entry("k") is None


# ── 报告层：结构完整 + 分布统计 ─────────────────────────────────────────
def test_build_perf_report_structure(tmp_path):
    v = GateVerdict(status=GATE_PASS, target="demo_t", key="k",
                    kind=KIND_API, metrics={"elapsed_ms": 100.0})
    report = build_perf_report("demo_t", "run1", [v], [_sample()],
                               {"version": 1, "entries_count": 1, "path": str(tmp_path)})
    assert report["target"] == "demo_t" and report["run_id"] == "run1"
    assert report["gate_summary"]["pass"] == 1
    assert report["status_distribution"][GATE_PASS] == 1
    assert report["items"][0]["key"] == "k"


# ── 集成层：跨运行回归对比 ─────────────────────────────────────────────
def _mk_report(run_id, statuses, key_base="k"):
    items = [GateVerdict(status=s, target="demo_t", key=f"{key_base}{i}",
                         kind=KIND_API, metrics={"elapsed_ms": 100.0})
             for i, s in enumerate(statuses)]
    return build_perf_report("demo_t", run_id, items, [_sample()])


def test_regression_compare_detects_critical_degrade():
    prev = _mk_report("r_prev", [GATE_PASS, GATE_PASS])
    # 本轮第 2 个 key 从 PASS → FAIL，且慢 300%（≥20% 退化）→ critical
    now = _mk_report("r_now", [GATE_PASS, "FAIL"])
    now["items"][1]["metrics"] = {"elapsed_ms": 400.0}
    now["items"][1]["status"] = "FAIL"
    now["gate_summary"]["fail"] = 1
    res = compare(now, prev, degrade_pct=20.0)
    assert res["regression_summary"]["has_regression"] is True
    assert res["regression_summary"]["total_keys_compared"] == 2


def test_regression_compare_with_history(tmp_path):
    # 先存一份历史报告
    report = build_perf_report("demo_t", "r_prev", [
        GateVerdict(status=GATE_PASS, target="demo_t", key="k", kind=KIND_API,
                    metrics={"elapsed_ms": 100.0})], [_sample()])
    import json
    out = tmp_path / "demo_t_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    # 无历史目录 → info
    empty = compare_with_history(_mk_report("r1", [GATE_PASS]), report_dir=str(tmp_path / "empty"))
    assert empty["regression_summary"]["total_keys_compared"] == 0
    # 有历史 → 正常回比
    res = compare_with_history(_mk_report("r2", [GATE_PASS]), report_dir=str(tmp_path))
    assert "regression_summary" in res


def test_save_regression_report(tmp_path):
    report = _mk_report("r1", [GATE_PASS])
    path = save_regression_report(compare(report, report), str(tmp_path))
    assert path and __import__("os").path.isfile(path)


# ── 差距4(2026-09-02)：evaluate_perf_gate 门禁判定后必须回写基线 ──────────
def test_evaluate_perf_gate_writes_back_baseline(monkeypatch):
    """黑盒断言：采集→门禁→基线回写 闭环，而不是只 load 基线做判定。

    root：evaluate_perf_gate 原先只 BaselineManager.load() 读基线做相对判定，
    从不 update()，导致基线永为 BASELINE_ESTABLISHED、回归对比无历史可比。
    修复后有效样本在门禁判定后必须真正回写基线（跨靶子按 target 分文件）。
    通用性：不认业务，只断言「有效样本 → bm.update 被调用」，且无效样本不得触发。
    """
    from src.pipeline.steps.step4.empirical import perf_checker

    recorded: dict = {"updates": []}

    class _FakeBM:
        def __init__(self, target):  # noqa: D401
            self.target = target

        def load(self):
            return {"entries": {}}

        def version(self) -> int:
            return 0  # 首轮（无基线）→ 策略应自动建立基线

        def update(self, samples, run_id=""):
            recorded["updates"].append((self.target, list(samples), run_id or ""))

    # evaluate_perf_gate 在函数体内 `from ... import BaselineManager`，
    # 需 patch 底层模块属性而非 perf_checker 模块属性
    monkeypatch.setattr(
        "src.extensions.perf_baseline.baseline.BaselineManager", _FakeBM)

    valid = {"kind": "api", "key": "api::GET /items",
             "target": "demo_t", "metrics": {"elapsed_ms": 100.0, "status_code": 200}}
    invalid = {"kind": "api", "key": "api::GET /bad", "target": "demo_t",
               "error": "boom"}

    resp = perf_checker.evaluate_perf_gate(
        [valid, invalid], {"target_key": "demo_t"}, run_id="r1")

    # 门禁判定本身正常产出
    assert resp["enabled"] is True and len(resp["verdicts"]) == 2

    # 只有有效样本回写基线；无效样本绝不污染（见 BaselineManager.update）
    assert len(recorded["updates"]) == 1
    tgt, samples, run_id = recorded["updates"][0]
    assert tgt == "demo_t"
    assert run_id == "r1"
    assert len(samples) == 1
    assert samples[0].key == valid["key"]