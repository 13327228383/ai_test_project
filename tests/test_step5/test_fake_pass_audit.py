# -*- coding: utf-8 -*-
"""假绿审计·正确口径测试：把「无证据(真假绿)」与「低置信带证据(需复核)」分开，
避免把真实带证据的通过误标为假 PASS（如 hospital_sut 4.76% 误标）。
镜像 src/pipeline/steps/step5/fake_pass_audit.py。
"""
import pytest

from src.pipeline.steps.step5 import fake_pass_audit as fpa


# ─────────────────────────────────────────────────────────────
# _classify_pass 三态
# ─────────────────────────────────────────────────────────────
def test_classify_no_evidence():
    assert fpa._classify_pass(0.9, "", "", "") == "no_evidence"
    assert fpa._classify_pass(0.66, "", "", "") == "no_evidence"
    assert fpa._classify_pass(0.9, None, None, None) == "no_evidence"


def test_classify_low_confidence_with_evidence():
    # 有实质证据即使 conf<0.70 也只是"低置信带证据"，不是假绿
    assert fpa._classify_pass(0.667, "期望", "实际", "细节") == "low_confidence"
    assert fpa._classify_pass(0.0, "e", "a", "d") == "low_confidence"


def test_classify_ok():
    assert fpa._classify_pass(0.71, "e", "a", "d") == "ok"
    assert fpa._classify_pass(0.90, "e", "a", "d") == "ok"


# ─────────────────────────────────────────────────────────────
# audit_fake_passes 分类计数（无证据=假绿；低置信带证据=复核）
# ─────────────────────────────────────────────────────────────
class _FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params):
        return _FakeRows(self._rows)

    def close(self):
        pass


def _run_audit(monkeypatch, rows):
    import src.core.db_manager as dbm

    monkeypatch.setattr(dbm, "get_connection", lambda: _FakeConn(rows))
    return fpa.audit_fake_passes("demo")


def test_audit_separates_no_evidence_and_low_confidence(monkeypatch):
    rows = [
        # 真·无证据假绿
        {"id": 1, "case_id": "C1", "confidence": 0.9, "expected": "", "actual": "", "details": ""},
        # 有证据但低置信 → 复核，不计假绿
        {"id": 2, "case_id": "C2", "confidence": 0.66, "expected": "e", "actual": "a", "details": "d"},
        # 正常通过
        {"id": 3, "case_id": "C3", "confidence": 0.9, "expected": "e", "actual": "a", "details": "d"},
    ]
    r = _run_audit(monkeypatch, rows)
    assert r["total_passes"] == 3
    assert r["no_evidence_passes"] == 1
    assert r["low_confidence_review"] == 1
    assert r["fake_passes"] == 1
    assert r["rate"] == pytest.approx(1 / 3)
    assert [h["case_id"] for h in r["hits"]] == ["C1"]  # 仅无证据进 hits
    assert [rv["case_id"] for rv in r["reviews"]] == ["C2"]  # 低置信进 reviews


def test_audit_low_confidence_with_evidence_not_flagged_as_fake(monkeypatch):
    """回归用例：hospital_sut TC-REQ-002-I01 conf=0.667 但证据齐全，
    修复前被误标假绿(假绿率4.76%)；修复后为复核项，假绿率=0。"""
    rows = [
        {"id": 1315, "case_id": "TC-REQ-002-I01", "confidence": 0.667,
         "expected": "[PASS] text_contains: 找到1个",
         "actual": "[PASS] Agent(guided): 验证通过",
         "details": '[{"track": "ui", "passed": true}]'},
    ]
    r = _run_audit(monkeypatch, rows)
    assert r["fake_passes"] == 0
    assert r["rate"] == 0.0
    assert r["low_confidence_review"] == 1
    assert r["alert"] is False


def test_summarize_includes_both_sections(monkeypatch):
    rows = [
        {"id": 1, "case_id": "C1", "confidence": 0.9, "expected": "", "actual": "", "details": ""},
        {"id": 2, "case_id": "C2", "confidence": 0.6, "expected": "e", "actual": "a", "details": "d"},
    ]
    md = fpa.summarize_fake_pass_audit(_run_audit(monkeypatch, rows))
    assert "无证据假 PASS（真·假绿）" in md
    assert "低置信但带证据（需人工复核）" in md
    assert "C1" in md and "C2" in md


# ─────────────────────────────────────────────────────────────
# enforce_fake_pass_block 只拦截真·无证据（dry_run 大流量安全）
# ─────────────────────────────────────────────────────────────
def test_enforce_only_downgrades_no_evidence(monkeypatch):
    import src.users.audit as ua

    rows = [
        {"id": 1, "case_id": "C1", "confidence": 0.9, "expected": "", "actual": "", "details": ""},
        # 低置信带证据 → 不应被自动降级为 FAIL
        {"id": 2, "case_id": "C2", "confidence": 0.6, "expected": "e", "actual": "a", "details": "d"},
    ]
    # _cfg 在 fpa 模块内已绑定为 _read_config_value，需直接 patch fpa._cfg。
    # ENFORCE=True 开启自动拦截；ALERT_RATE 走默认 0.05。
    monkeypatch.setattr(
        fpa, "_cfg", lambda k, d=None: True if k == "FAKE_PASS_AUDIT_ENFORCE" else d
    )
    import src.core.db_manager as dbm

    monkeypatch.setattr(dbm, "get_connection", lambda: _FakeConn(rows))
    updated = []
    monkeypatch.setattr(ua, "update_execution_status",
                        lambda eid, st, reason: (updated.append((eid, st)) or True))
    r = fpa.enforce_fake_pass_block("demo", dry_run=False)
    assert r["enforced"] is True
    assert r["enforced_ids"] == [1]
    # 仅无证据的 exec_id=1 被降级，低置信带证据的 2 保持 PASS
    assert updated == [(1, "FAIL")]


def test_enforce_dry_run_default_is_safe(monkeypatch):
    rows = [
        {"id": 1, "case_id": "C1", "confidence": 0.9, "expected": "", "actual": "", "details": ""},
    ]
    import src.core.db_manager as dbm

    monkeypatch.setattr(dbm, "get_connection", lambda: _FakeConn(rows))
    r = fpa.enforce_fake_pass_block("demo")  # 默认 dry_run=True
    assert r["enforced"] is False
    assert r["enforced_ids"] == []


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))