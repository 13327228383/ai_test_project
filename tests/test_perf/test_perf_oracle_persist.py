# -*- coding: utf-8 -*-
"""性能 oracle 唯一事实源落库测试（2026-09-03 通用性修复）。

背景：三横切门禁 a11y→oracle_results(accessibility)、visual→visual_review 均落
MySQL 唯一事实源，唯 perf 原先只进日志/报告。本修复把 run 级 perf 门禁结果纳入
通用 oracle_results(oracle_type=performance)，与 a11y 走同一 write_oracle_result 入口。

覆盖：
  1. write_oracle_result 白名单放行 oracle_type=performance；
  2. write_perf_result_from_gate 的 verdict/score/空数据派生语义（黑盒断言）；
  3. serial._persist_cross_cut_perf 转发链路（project/execution_id=0、禁用时跳过）。
"""
from __future__ import annotations

import pytest

from src.oracle import store as oracle_store


# ── 白名单放行 performance ───────────────────────────────────────────
def test_oracle_whitelist_allows_performance(monkeypatch):
    captured = {}

    class _Conn:
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def commit(self):
            pass

        def close(self):
            pass

        def rollback(self):
            pass

    monkeypatch.setattr("src.core.db_manager.get_connection", lambda: _Conn())

    ok = oracle_store.write_oracle_result(
        project="proj", version_id=0, execution_id=0,
        oracle_type="performance", verdict="pass", score=0.9,
        detail={"run_id": "r1"}, run_id="r1",
    )
    assert ok is True
    # 2026-09-20 起新增 run_id 列（run 级 execution_id=0 行必须携带真实 run_id，
    # 否则 Step5 聚合 INNER JOIN executions 时丢弃横切墙结果）→ oracle_type/verdict 后移一位。
    assert captured["params"][3] == "r1"
    assert captured["params"][4] == "performance"
    assert captured["params"][5] == "pass"


# ── write_perf_result_from_gate 派生语义（黑盒：patch 落库入口）──────────
def _capture(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append(kw)
        return True

    monkeypatch.setattr(oracle_store, "write_oracle_result", fake)
    return calls


def _gate(verdicts, blocking=False, total=None):
    return {
        "run_id": "r1",
        "verdicts": verdicts,
        "summary": {
            "blocking": blocking,
            "total": total if total is not None else len(verdicts),
        },
    }


def _v(status, key="k", kind="api", metrics=None):
    return {"key": key, "kind": kind, "status": status,
            "metrics": metrics or {"elapsed_ms": 100.0},
            "violations": []}


def test_perf_gate_all_pass(monkeypatch):
    calls = _capture(monkeypatch)
    ok = oracle_store.write_perf_result_from_gate("proj", 0, 0, _gate([_v("PASS")]))
    assert ok is True
    kw = calls[0]
    assert kw["oracle_type"] == "performance"
    assert kw["verdict"] == "pass"
    assert kw["score"] == 1.0
    assert kw["detail"]["pass"] == 1


def test_perf_gate_fail_when_blocking_or_fail(monkeypatch):
    calls = _capture(monkeypatch)
    oracle_store.write_perf_result_from_gate(
        "proj", 0, 0, _gate([_v("FAIL")], blocking=False))
    assert calls[0]["verdict"] == "fail"
    assert calls[0]["score"] == 0.0

    calls.clear()
    oracle_store.write_perf_result_from_gate(
        "proj", 0, 0, _gate([_v("PASS")], blocking=True))
    assert calls[0]["verdict"] == "fail"   # blocking 优先


def test_perf_gate_warn_on_pre_gate_baseline(monkeypatch):
    calls = _capture(monkeypatch)
    oracle_store.write_perf_result_from_gate(
        "proj", 0, 0, _gate([_v("BASELINE_ESTABLISHED"), _v("PRE_GATE")]))
    kw = calls[0]
    assert kw["verdict"] == "warn"
    assert kw["score"] == 0.0            # 无 PASS key
    assert kw["detail"]["baseline_established"] == 1
    assert kw["detail"]["pre_gate"] == 1


def test_perf_gate_no_verdicts_no_write(monkeypatch):
    calls = _capture(monkeypatch)
    ok = oracle_store.write_perf_result_from_gate("proj", 0, 0, {})
    assert ok is False and calls == []    # 无样本不写空记录


# ── serial 转发链路 ──────────────────────────────────────────────────
def test_serial_persist_cross_cut_perf_forwards(monkeypatch):
    from src.pipeline.steps.step4.browser_core import serial as serial_mod
    from src.pipeline.steps.step4.browser_core.serial import _BrowserCoreSerialMixin as Mixin

    forwarded = {}

    def fake_write(project, version_id, execution_id, gate):
        forwarded.update(project=project, version_id=version_id,
                         execution_id=execution_id, gate_keys=list((gate or {}).keys()))
        return True

    monkeypatch.setattr(serial_mod, "config", type("_C", (), {"project_name": "proj"})())
    monkeypatch.setattr(oracle_store, "write_perf_result_from_gate", fake_write)
    monkeypatch.setattr("src.core.versioning.current_version_id", lambda _p: 7)

    inst = object.__new__(Mixin)
    inst._persist_cross_cut_perf(
        {"perf": {"enabled": True, "verdicts": [_v("PASS")], "summary": {"total": 1}}})
    assert forwarded["project"] == "proj"
    assert forwarded["execution_id"] == 0
    assert "verdicts" in forwarded["gate_keys"]


def test_serial_persist_cross_cut_perf_disabled_skips(monkeypatch):
    from src.pipeline.steps.step4.browser_core import serial as serial_mod
    from src.pipeline.steps.step4.browser_core.serial import _BrowserCoreSerialMixin as Mixin

    hit = []

    def fake_write(*a, **kw):
        hit.append(a)
        return True

    monkeypatch.setattr(serial_mod, "config", type("_C", (), {"project_name": "proj"})())
    monkeypatch.setattr(oracle_store, "write_perf_result_from_gate", fake_write)

    inst = object.__new__(Mixin)
    inst._persist_cross_cut_perf({"perf": {"enabled": False, "verdicts": []}})
    assert hit == []    # perf 未启用时不落库


def test_serial_persist_fail_safe_when_project_missing(monkeypatch):
    from src.pipeline.steps.step4.browser_core import serial as serial_mod
    from src.pipeline.steps.step4.browser_core.serial import _BrowserCoreSerialMixin as Mixin

    monkeypatch.setattr(serial_mod, "config", type("_C", (), {"project_name": ""})())
    inst = object.__new__(Mixin)
    # 不抛异常即为通过（fail-safe）
    inst._persist_cross_cut_perf({"perf": {"enabled": True}})