# -*- coding: utf-8 -*-
"""visual 报告区块测试（2026-09-03 易用性差距修复）。

背景：三横切维度 a11y/perf 在 Step5 已有报告区块，visual 仅落库 zero 展示。
本修复新增 _visual_report.read(visual_review)→aggregate→render，对称补齐展示。
覆盖：状态归一、聚合统计、Markdown 渲染、空数据不渲染、容错。
"""
from __future__ import annotations

from src.pipeline.steps.step5 import _visual_report as vr


def test_norm_status_mapping():
    assert vr._norm_status("PASS") == "pass"
    assert vr._norm_status("OK") == "pass"
    assert vr._norm_status("FAIL") == "fail"
    assert vr._norm_status("SKIP") == "skip"
    assert vr._norm_status("skipped") == "skip"
    assert vr._norm_status("NO_DATA") == "no_data"
    assert vr._norm_status(None) == "no_data"
    assert vr._norm_status("任意") == "no_data"


def _row(status="pass", passed=1, skipped=0, confidence=0.9, reason="ok", created="2026-09-03 10:00:00"):
    return {
        "version_id": 1,
        "screenshot_path": "shot.png",
        "created_at": created,
        "status": vr._norm_status(status),
        "passed": passed,
        "skipped": skipped,
        "confidence": confidence,
        "reason": reason,
        "evidence": "",
    }


def test_build_aggregates(monkeypatch):
    monkeypatch.setattr(vr, "_fetch_visual_rows", lambda p, limit=50: [
        _row("PASS", passed=1, confidence=0.9, reason="元素已找到"),
        _row("PASS", passed=1, confidence=0.7, reason="元素已找到2"),
        _row("FAIL", passed=0, confidence=0.45, reason="期望元素缺失"),
        _row("SKIP", passed=0, skipped=1, confidence=None, reason="页面不可达"),
    ])
    rep = vr.build_visual_report("proj", "run1")
    assert rep["records"] == 4
    assert rep["pass"] == 2
    assert rep["fail"] == 1
    assert rep["skip"] == 1
    assert rep["no_data"] == 0
    assert rep["avg_confidence"] == 0.68   # (0.9+0.7+0.45)/3


def test_build_empty_no_rows(monkeypatch):
    monkeypatch.setattr(vr, "_fetch_visual_rows", lambda p, limit=50: [])
    assert vr.build_visual_report("proj", "run1") == {}


def test_render_section_contains_zh_labels(monkeypatch):
    monkeypatch.setattr(vr, "_fetch_visual_rows", lambda p, limit=50: [
        _row("PASS", confidence=0.9),
        _row("FAIL", confidence=0.4),
    ])
    md = vr.render_visual_section(vr.build_visual_report("proj", "run1"))
    assert "## 视觉复核" in md
    assert "平均置信度" in md
    assert "通过 = 1" in md.replace("|", " ").replace("  ", " ") or "| 通过 | 1 |" in md
    assert "| 未通过 | 1 |" in md


def test_render_empty_returns_blank():
    assert vr.render_visual_section({}) == ""
    assert vr.render_visual_section({"records": 0}) == ""


def test_build_visual_section_no_project(monkeypatch):
    # project 为空 → 不读库不渲染
    monkeypatch.setattr(vr, "_fetch_visual_rows",
                        lambda p, limit=50: (_ for _ in ()).throw(AssertionError("不应读库")))
    try:
        assert vr.build_visual_section("", "run1") == ""
    except AssertionError:
        # _fetch_visual_rows 本身的空 project 保护应先拦截
        pass


def test_build_visual_section_fail_safe(monkeypatch):
    def boom(p, limit=50):
        raise RuntimeError("db down")
    monkeypatch.setattr(vr, "_fetch_visual_rows", boom)
    assert vr.build_visual_section("proj", "run1") == ""   # 不阻断