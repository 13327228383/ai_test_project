# -*- coding: utf-8 -*-
"""契约：项目级清空范围由 schema 派生，保留集只放注册表与输入态。

缺陷形状: 清空范围曾以手写 if 链逐表维护（``_clear.py`` 内 45 条 ``DELETE FROM``），
新增表不会被清 → "从干净态跑流水线"无法达成，且每补一张表就多一个分支。

本测试锁定:
1. 清空范围 = 带 ``project``/``project_name`` 列的表 − 保留集，不允许静默遗漏；
2. 保留集只含注册/身份表与输入态表，派生数据表进入保留集即失败；
3. ``include_inputs`` 控制输入态开关，引擎状态行恒清；
4. ``dry_run`` 只统计不删除，且外键约束在结束后恢复；
5. ``mysql://`` 目标不能指向非项目级表。
"""

from __future__ import annotations

import pytest

from src.core import project_purge as pp

PROBE = "__purge_gate_probe__"


def _conn_or_skip():
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        conn.execute("SELECT 1").fetchone()
        return conn
    except Exception as e:  # noqa: BLE001
        pytest.skip("数据库不可用，跳过 schema 派生契约: %s" % e)


def test_purge_scope_is_derived_from_schema():
    conn = _conn_or_skip()
    try:
        scoped = pp.project_scoped_tables(conn)
    finally:
        conn.close()
    assert scoped, "未能从 information_schema 派生任何项目级表"

    plan = pp.purge_plan(PROBE)
    planned = {p["table"] for p in plan}
    keep = set(pp.KEEP_REGISTRY_TABLES) | set(pp.KEEP_INPUT_TABLES)
    missing = {t for t in scoped if t not in keep} - planned
    assert not missing, "这些派生表未被清空计划覆盖: %s" % sorted(missing)

    full_clear = {p["table"] for p in plan if p["label"] == p["table"]}
    assert not (full_clear & set(pp.KEEP_REGISTRY_TABLES)), (
        "注册表被整表清空: %s" % sorted(full_clear & set(pp.KEEP_REGISTRY_TABLES))
    )
    for t in pp.SCOPE_KEYED_TABLES:
        assert t in planned, "scope 键表未纳入清空: %s" % t


def test_keep_sets_hold_registry_and_inputs_only():
    derived = {
        "executions", "oracle_results", "test_cases", "requirements",
        "requirement_slices", "case_meta", "case_requirement_mapping",
        "coverage_results", "generated_reports", "run_summary", "run_logs",
        "pipeline_checkpoints", "visual_review", "visual_baselines",
        "execution_screenshots", "review_lane", "review_log", "golden_set",
        "trace_links", "multimodal_chunks", "flaky_guard_history",
    }
    assert not derived & (set(pp.KEEP_REGISTRY_TABLES) | set(pp.KEEP_INPUT_TABLES)), (
        "派生数据表被放进保留集（会导致清不干净）"
    )
    assert {"project_configs", "project_versions", "api_tokens"} <= set(pp.KEEP_REGISTRY_TABLES)
    assert {"source_documents", "source_hashes", "figma_pages"} <= set(pp.KEEP_INPUT_TABLES)


def test_input_tables_follow_include_inputs():
    default = pp.purge_plan(PROBE)
    full = pp.purge_plan(PROBE, include_inputs=True)
    assert "document_versions" not in {p["table"] for p in default}
    assert "document_versions" in {p["table"] for p in full}
    assert "source_documents[derived_doc_types]" in {p["label"] for p in default}


def test_dry_run_counts_without_deleting_and_restores_fk_checks():
    conn = _conn_or_skip()
    try:
        before = conn.execute(
            "SELECT COUNT(*) c FROM requirements WHERE project=%s", (PROBE,)
        ).fetchone()["c"]
    finally:
        conn.close()

    result = pp.purge_project(PROBE, dry_run=True)
    assert result["dry_run"] is True and result["errors"] == []
    assert result["total"] == sum(result["tables"].values())

    conn = _conn_or_skip()
    try:
        after = conn.execute(
            "SELECT COUNT(*) c FROM requirements WHERE project=%s", (PROBE,)
        ).fetchone()["c"]
        fk = conn.execute("SELECT @@SESSION.foreign_key_checks AS v").fetchone()["v"]
    finally:
        conn.close()
    assert int(before) == int(after)
    assert int(fk) == 1, "清空后外键约束未恢复，会污染连接池里的其它会话"


def test_resolve_spec_rejects_non_project_tables():
    conn = _conn_or_skip()
    try:
        for spec in ("mysql://users", "mysql://projects", "mysql://no_such_table_xyz"):
            assert pp.resolve_spec(conn, spec) is None, "不得删除非项目级表: %s" % spec
        item = pp.resolve_spec(conn, "mysql://source_documents/confluence")
        assert item and item["table"] == "source_documents" and "doc_type" in item["where"]
    finally:
        conn.close()


def test_resolve_spec_maps_virtual_table_and_key_columns():
    conn = _conn_or_skip()
    try:
        item = pp.resolve_spec(conn, "mysql://screenshots/TC-REQ-001-I01")
        assert item["table"] == "execution_screenshots" and "case_id" in item["where"]
        assert "run_id" in pp.resolve_spec(conn, "mysql://run_logs/step1_verify__v1_0")["where"]
        assert "`id`" in pp.resolve_spec(conn, "mysql://run_logs/12345")["where"]
        item = pp.resolve_spec(
            conn, "mysql://source_documents/confluence", version_label="v1_0"
        )
        assert "version_label" in item["where"] and item["args"][-1] == "v1_0"
        item = pp.resolve_spec(conn, "mysql://execution_screenshots/7", version_label="v1_0")
        assert "version_label" not in item["where"]
    finally:
        conn.close()
