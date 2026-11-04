# -*- coding: utf-8 -*-
"""流水线进度 DB 查询：从数据库产物推断已完成的最大步骤号。

从 pipeline_progress.py 拆分（§4.3 单文件≤800行红线）。
逻辑不变，仅结构重构。
"""
import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


def infer_step_from_db(project, version_id=0, version_label=""):
    """从数据库产物推断已完成的最大步骤号。

    逻辑：按 Step1→Step5 顺序检查各步骤的产物表是否有数据。
    只要某步骤有产物，该步骤就算已完成。
    返回 (max_completed_step, per_step_counts)。

    ★ 按 (project, version_id) 隔离；source_documents 用 version_label。
    """
    if not project:
        return 0, {}
    per_step_counts = {}
    max_step = 0
    with guard("infer_step_from_db: from src.core.db_manager import get_", log=_log):
        from src.core.db_manager import get_connection, release_connection

        conn = None
        try:
            conn = get_connection()
            # Step1: 数据源数量与阻断判定同口径（step1_source_keys），
            # 不得遍历 sources_status 全量键（含 rag_coverage 等元数据会虚增）
            _step1_source_count = 0
            with guard("infer_step_from_db: _status_row = conn.execute( 'SELECT ", log=_log):
                _status_row = conn.execute(
                    "SELECT content FROM source_documents "
                    "WHERE project=%s AND version_label=%s AND doc_type='step1_status' "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (project, version_label),
                ).fetchone()
                if _status_row:
                    import json as _json

                    from src.pipeline._step1_phases import (
                        count_configured_step1_sources,
                    )

                    _sources_status = _json.loads(_status_row.get("content") or "{}")
                    _step1_source_count = count_configured_step1_sources(
                        _sources_status if isinstance(_sources_status, dict) else {}
                    )
            if _step1_source_count:
                max_step = max(max_step, 1)
                per_step_counts[1] = _step1_source_count

            # Step2: requirements
            r2 = conn.execute(
                "SELECT COUNT(*) AS cnt FROM requirements "
                "WHERE project=%s AND version_id=%s",
                (project, version_id),
            ).fetchone()
            if r2 and r2["cnt"]:
                max_step = max(max_step, 2)
                per_step_counts[2] = r2["cnt"]

            # Step3: test_cases（★ 修复割裂：排除 stale 和 retired，与 stats 口径一致）
            r3 = conn.execute(
                "SELECT COUNT(*) AS cnt FROM test_cases "
                "WHERE project=%s AND version_id=%s "
                "AND valid_status NOT IN ('stale', 'retired')",
                (project, version_id),
            ).fetchone()
            if r3 and r3["cnt"]:
                max_step = max(max_step, 3)
                per_step_counts[3] = r3["cnt"]

            # Step4: executions（★ 修复割裂：要求 case_id 在当前 test_cases 表中，与 stats 口径一致）
            r4 = conn.execute(
                "SELECT COUNT(*) AS cnt FROM executions e "
                "INNER JOIN test_cases t ON e.case_id=t.case_id "
                "AND e.project=t.project AND e.version_id=t.version_id "
                "WHERE e.project=%s AND e.version_id=%s "
                "AND t.valid_status <> 'stale'",
                (project, version_id),
            ).fetchone()
            if r4 and r4["cnt"]:
                max_step = max(max_step, 4)
                per_step_counts[4] = r4["cnt"]

            # Step5: generated_reports
            with guard("infer_step_from_db: r5 = conn.execute( 'SELECT COUNT(*) ", log=_log):
                r5 = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM generated_reports "
                    "WHERE project=%s AND version_id=%s",
                    (project, version_id),
                ).fetchone()
                if r5 and r5["cnt"]:
                    max_step = max(max_step, 5)
                    per_step_counts[5] = r5["cnt"]
        finally:
            if conn:
                release_connection(conn)
    return max_step, per_step_counts
