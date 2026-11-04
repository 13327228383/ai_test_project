# -*- coding: utf-8 -*-
"""回归测试：方案B —— TargetEmpiricalVerifier 报告回写 executions（MySQL 唯一事实源）。

不依赖浏览器/DB：monkeypatch audit.record_execution，验证 _record_cases_to_executions
对三种形态 case（overall / 布尔 pass / blocked）的 status/expected/actual/failure_category
映射正确，且 _persist_report 先写 JSON 再回写。
"""
import json

from src.pipeline.steps.step4.empirical.target_empirical import TargetEmpiricalVerifier


def _make_v():
    return TargetEmpiricalVerifier({"target_key": "hospital_sut",
                                    "url": "http://127.0.0.1:5080",
                                    "run_id": "u_test_20260902"})


def test_case_status_mapping(monkeypatch):
    captured = {}

    def fake_record(**kw):
        captured[kw["case_id"]] = kw

    monkeypatch.setattr(
        "src.users.audit.record_execution", fake_record)
    v = _make_v()
    v._record_cases_to_executions({"cases": [
        {"case": "P01", "overall": "PASS"},
        {"case": "N01", "overall": "FAIL",
         "assertions": [{"name": "拒绝", "expected": "拒绝", "actual": "未拒绝", "pass": False}]},
        {"case": "B01", "pass": False, "blocked_by": "no_cookie", "case_id": "B01"},
        {"case": "M01", "overall": "PASS", "confidence": 0.83,
         "assertions": [{"name": "a", "expected": "1", "actual": "1", "pass": True}]},
    ]})
    assert captured["P01"]["status"] == "PASS"
    assert captured["N01"]["status"] == "FAIL"
    assert captured["B01"]["status"] == "SKIP"
    assert captured["N01"]["failure_category"] == "PRODUCT_BUG"
    assert captured["B01"]["failure_category"] == "ENVIRONMENT_ERROR"
    assert captured["N01"]["expected"] == "拒绝: 拒绝"
    assert captured["N01"]["actual"] == "拒绝: 未拒绝"
    # 差距2修复：empirical 回写必须携带客观置信度，不再留 0.0/None
    assert captured["P01"]["confidence"] == 0.9        # 无断言 PASS → 0.9（真实检测）
    assert captured["N01"]["confidence"] == 0.0        # 1 条已判定断言未过 → 0.0
    assert captured["B01"]["confidence"] == 0.0        # blocked/SKIP → 0.0（未完成检测）
    assert captured["M01"]["confidence"] == 0.83       # 显式 confidence 优先
    for cid, rec in captured.items():
        assert rec["run_id"] == "u_test_20260902"
        assert rec["check_type"] == "empirical"


def test_empty_cases_no_write(monkeypatch):
    called = []

    def fake_record(**kw):
        called.append(kw)

    monkeypatch.setattr(
        "src.users.audit.record_execution", fake_record)
    v = _make_v()
    v._record_cases_to_executions({"cases": [], "summary": {"all_pass": False}})
    assert called == []


def test_persist_report_writes_json_then_executions(tmp_path, monkeypatch):
    written = {}

    def fake_write(report, path):
        written["json"] = json.dumps(report, ensure_ascii=False)

    monkeypatch.setattr(
        "src.pipeline.steps.step4.empirical.target_empirical.report_write", fake_write)
    recs = []

    def fake_record(**kw):
        recs.append(kw)

    monkeypatch.setattr(
        "src.users.audit.record_execution", fake_record)
    v = _make_v()
    v.report_path = str(tmp_path / "r.json")
    v._persist_report({"cases": [{"case": "P01", "overall": "PASS"}]})
    assert "json" in written
    assert len(recs) == 1
    assert recs[0]["case_id"] == "P01"