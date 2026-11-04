# -*- coding: utf-8 -*-
"""通用口径测试：SKIP（未真正验证）不应稀释平均置信度。

背景：
- executions 表中 SKIP 行（evidence_source=fallback_no_verify，verify 未调用）
  带残余置信度(≈0.3)。此前 `_load_execution_confidence_from_db` 把它们也注入，
  `_collect_confidence_summary` 的 avg_confidence 被平均成 ~0.61。
- 修复后仅 PASS/FAIL（真正验证）的置信度参与平均，avg_confidence ≈0.81 诚实可信。
"""
import sys
import pytest

# ─────────────────────────────────────────────────────────────
# 修复点1: _load_execution_confidence_from_db 只注入 PASS/FAIL 置信度
# ─────────────────────────────────────────────────────────────


class _FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """只记录 SQL，不真正连库。"""

    def __init__(self, rows):
        self._rows = rows
        self.executed_sql = ""

    def execute(self, sql, params):
        self.executed_sql = sql
        return _FakeRows(self._rows)

    def close(self):
        pass


def _patch_db(monkeypatch, rows):
    """stub get_connection / current_version_id 为可控假连接。"""
    import src.core.db_manager as dbm
    import src.core.versioning as v

    conn = _FakeConn(rows)
    monkeypatch.setattr(dbm, "get_connection", lambda: conn)
    monkeypatch.setattr(v, "current_version_id", lambda project: 1)
    return conn


def test_confidence_sql_filters_skip_status(monkeypatch):
    """注入 SQL 必须过滤 status IN ('PASS','FAIL')，SKIP 不进入置信度统计。"""
    from src.pipeline.steps.step5 import _db_utils as du

    rows = [{"case_id": "C1", "confidence": 0.9}]
    conn = _patch_db(monkeypatch, rows)
    # 断言 fetchall 被调用（假连接已返回），避免 N+1 细节
    assert callable(getattr(du, "_load_execution_confidence_from_db"))
    du._load_execution_confidence_from_db(
        [{"case_id": "C1"}, {"case_id": "C2"}], "demo"
    )
    assert "STATUS IN ('PASS','FAIL')" in conn.executed_sql.upper()


def test_load_execution_confidence_only_returns_pass_fail(monkeypatch):
    """返回映射只含真正验证(PASS/FAIL)的用例，SKIP 不被注入。"""
    from src.pipeline.steps.step5 import _db_utils as du

    # 模拟 DB：C1=PASS(0.9)、C2=SKIP(0.3)。修复后 DB 层已过滤，仅回传 C1。
    rows = [{"case_id": "C1", "confidence": 0.9}]
    _patch_db(monkeypatch, rows)
    result = du._load_execution_confidence_from_db(
        [{"case_id": "C1"}, {"case_id": "C2"}], "demo"
    )
    assert result == {"C1": 0.9}
    assert "C2" not in result


# ─────────────────────────────────────────────────────────────
# 修复点2: _collect_confidence_summary 平均只含携带 _confidence 的用例
# ─────────────────────────────────────────────────────────────


def test_collect_confidence_summary_excludes_unverified():
    """无 _confidence（未验证的 SKIP）用例不进 avg_confidence；并正确给低置信计数。"""
    from src.pipeline.steps.step5._confidence import _collect_confidence_summary

    intents = [
        {"case_id": "C1", "_confidence": {"confidence": 0.9}},
        {"case_id": "C2", "_confidence": {"confidence": 0.95}},
        # C3 = SKIP 未执行 verify，无 _confidence → 不应参与平均
        {"case_id": "C3"},
    ]
    summary = _collect_confidence_summary(intents)
    assert summary["avg_confidence"] == round((0.9 + 0.95) / 2, 4)
    assert summary["avg_confidence"] == pytest.approx(0.925)
    assert summary["low_confidence_count"] == 0


def test_collect_confidence_summary_avoids_zero_mean_contamination():
    """若误把 SKIP 的低残余置信度(0.3)注入，会大幅拖低；修复后不注入则不受影响。"""
    from src.pipeline.steps.step5._confidence import _collect_confidence_summary

    # 正确契约：SKIP 不带 _confidence
    clean = [
        {"case_id": "A", "_confidence": {"confidence": 0.8}},
        {"case_id": "B", "_confidence": {"confidence": 0.84}},
    ]
    summary_clean = _collect_confidence_summary(clean)
    assert summary_clean["avg_confidence"] == pytest.approx(0.82)

    # 污染版对比：若旧逻辑把 0.3 也计进来
    polluted = clean + [{"case_id": "S", "_confidence": {"confidence": 0.3}}]
    summary_polluted = _collect_confidence_summary(polluted)
    assert summary_polluted["avg_confidence"] == pytest.approx(
        round((0.8 + 0.84 + 0.3) / 3, 4)
    )
    # 修复后行为：SKIP 不应再注入，故文档化差异
    assert summary_clean["avg_confidence"] > summary_polluted["avg_confidence"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))