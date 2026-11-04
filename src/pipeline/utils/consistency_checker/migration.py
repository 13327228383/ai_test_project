# -*- coding: utf-8 -*-
from __future__ import annotations

"""
MySQL 事实源迁移健康度检查模块
=============================
检查 MySQL 事实源迁移健康度。只读，不修改数据。
"""

from pathlib import Path
from typing import Any

from src.pipeline.utils.consistency_helpers import (
    Issue,
    _make_issue,
    _load_mapping,
    _count_severity,
    _try_load_store_data,
    _try_health_stats,
    _check_mysql_store_tables,
    _check_mysql_trace_consistency,
    _check_mysql_mapping_consistency,
)
from src.pipeline.utils.consistency_checker.env import check_mysql_environment
import logging

_log = logging.getLogger(__name__)


def check_mysql_migration_health(
    excel_handler: Any | None = None,
    tracker: Any | None = None,
    requirement_store_obj: Any | None = None,
    case_store_obj: Any | None = None,
    trace_link_store_obj: Any | None = None,
    coverage_store_obj: Any | None = None,
    ai_generation_logs_store_obj: Any | None = None,
    review_store_obj: Any | None = None,
    action_library_store_obj: Any | None = None,
    figma_state_store_obj: Any | None = None,
    diff_snapshot_store_obj: Any | None = None,
) -> dict[str, Any]:
    """检查 MySQL 事实源迁移健康度。只读，不修改数据。

    """
    explicit_mysql_sources = (
        tracker is not None
        or requirement_store_obj is not None
        or case_store_obj is not None
        or trace_link_store_obj is not None
        or coverage_store_obj is not None
        or ai_generation_logs_store_obj is not None
        or review_store_obj is not None
        or action_library_store_obj is not None
        or figma_state_store_obj is not None
        or diff_snapshot_store_obj is not None
    )
    if excel_handler is None:
        from src.core.excel_handler import excel_handler as excel_handler
    if tracker is None:
        from src.pipeline.utils.case_requirement_tracker import (
            case_requirement_tracker as tracker,
        )
    if requirement_store_obj is None:
        from src.pipeline.utils.requirement_store import (
            requirement_store as requirement_store_obj,
        )
    if case_store_obj is None:
        from src.pipeline.utils.case_store import case_store as case_store_obj
    if trace_link_store_obj is None:
        try:
            from src.pipeline.utils.trace_link_store import (
                trace_link_store as trace_link_store_obj,
            )
        except Exception as _e:
            _log.debug("[migration] 非关键异常: %s", _e, exc_info=True)
            trace_link_store_obj = None
    if coverage_store_obj is None:
        try:
            from src.pipeline.utils.coverage_store import (
                coverage_store as coverage_store_obj,
            )
        except Exception as _e:
            _log.debug("[migration] 非关键异常: %s", _e, exc_info=True)
            coverage_store_obj = None
    if ai_generation_logs_store_obj is None:
        try:
            from src.pipeline.utils.ai_generation_logs_store import (
                ai_generation_logs_store as ai_generation_logs_store_obj,
            )
        except Exception as _e:
            _log.debug("[migration] 非关键异常: %s", _e, exc_info=True)
            ai_generation_logs_store_obj = None
    if review_store_obj is None:
        try:
            from src.pipeline.utils.review_store import review_store as review_store_obj
        except Exception as _e:
            _log.debug("[migration] 非关键异常: %s", _e, exc_info=True)
            review_store_obj = None
    if action_library_store_obj is None:
        try:
            from src.pipeline.utils.action_library_store import (
                action_library_store as action_library_store_obj,
            )
        except Exception as _e:
            _log.debug("[migration] 非关键异常: %s", _e, exc_info=True)
            action_library_store_obj = None
    if figma_state_store_obj is None:
        try:
            from src.pipeline.utils.figma_state_store import (
                figma_state_store as figma_state_store_obj,
            )
        except Exception as _e:
            _log.debug("[migration] 非关键异常: %s", _e, exc_info=True)
            figma_state_store_obj = None
    if diff_snapshot_store_obj is None:
        try:
            from src.pipeline.utils.diff_snapshot_store import (
                diff_snapshot_store as diff_snapshot_store_obj,
            )
        except Exception as _e:
            _log.debug("[migration] 非关键异常: %s", _e, exc_info=True)
            diff_snapshot_store_obj = None

    issues: list[Issue] = []
    env = check_mysql_environment()
    issues.extend(env.get("issues", []))
    users_dir = Path(__file__).resolve().parent.parent.parent.parent / "users"
    db_path = users_dir / "data.db"
    db_exists = db_path.is_file() or explicit_mysql_sources

    if db_exists:
        mysql_reqs, req_issues = _try_load_store_data(
            requirement_store_obj,
            "list_requirements",
            "MYSQL_REQUIREMENTS_READ_FAILED",
            "error",
            "MySQL requirements 读取失败",
            [],
        )
        mysql_intents, intent_issues = _try_load_store_data(
            case_store_obj,
            "list_intents",
            "MYSQL_VALIDATION_INTENTS_READ_FAILED",
            "error",
            "MySQL test_cases 读取失败",
            [],
        )
        trace_links_data, tl_issues = _try_load_store_data(
            trace_link_store_obj,
            "load_links",
            "MYSQL_TRACE_LINKS_READ_FAILED",
            "error",
            "MySQL trace_links 读取失败",
            {},
        )
        coverage_result, cov_issues = _try_load_store_data(
            coverage_store_obj,
            "load_coverage",
            "MYSQL_COVERAGE_RESULTS_READ_FAILED",
            "warning",
            "MySQL coverage_results 读取失败",
            {},
        )
        ai_generation_logs, aig_issues = _try_load_store_data(
            ai_generation_logs_store_obj,
            "load_logs",
            "MYSQL_AI_GENERATION_LOGS_READ_FAILED",
            "warning",
            "MySQL ai_generation_logs 读取失败",
            [],
        )
        issues.extend(req_issues)
        issues.extend(intent_issues)
        issues.extend(tl_issues)
        issues.extend(cov_issues)
        issues.extend(aig_issues)
    else:
        mysql_reqs = []
        mysql_intents = []
        trace_links_data = {}
        coverage_result = {}
        ai_generation_logs = []

    trace_links = trace_links_data if trace_link_store_obj is not None else {}
    cov_matrix = (
        coverage_result.get("matrix") if isinstance(coverage_result, dict) else None
    )

    excel_reqs = []
    excel_cases = []
    excel_fallback_ok = True
    try:
        excel_reqs = excel_handler.read_requirements()
    except Exception as e:
        excel_fallback_ok = False
        issues.append(
            _make_issue(
                "EXCEL_REQUIREMENTS_FALLBACK_FAILED",
                "error",
                "Excel 存量需求导入源读取失败",
                [str(e)[:200]],
            )
        )
    try:
        excel_cases = excel_handler.read_test_cases()
    except Exception as e:
        excel_fallback_ok = False
        issues.append(
            _make_issue(
                "EXCEL_TEST_CASES_FALLBACK_FAILED",
                "error",
                "Excel 存量测试用例导入源读取失败",
                [str(e)[:200]],
            )
        )

    if not mysql_reqs:
        issues.append(
            _make_issue(
                "MYSQL_REQUIREMENTS_EMPTY",
                "warning",
                "MySQL requirements 暂无数据，请先生成或执行 --sync-excel-to-mysql 回填",
            )
        )
    if not mysql_intents:
        issues.append(
            _make_issue(
                "MYSQL_VALIDATION_INTENTS_EMPTY",
                "warning",
                "MySQL test_cases 暂无数据，请先生成或执行 --sync-excel-to-mysql 回填",
            )
        )
    if not trace_links and trace_link_store_obj is not None:
        issues.append(
            _make_issue(
                "MYSQL_TRACE_LINKS_EMPTY",
                "warning",
                "MySQL trace_links 暂无数据（追溯链路是 Step2/Step3 覆盖率数据来源）",
            )
        )
    if (not cov_matrix or not cov_matrix) and coverage_store_obj is not None:
        issues.append(
            _make_issue(
                "MYSQL_COVERAGE_RESULTS_EMPTY",
                "warning",
                "MySQL coverage_results 暂无数据（Step3 覆盖矩阵是 Step5 报告的基础）",
            )
        )
    if not ai_generation_logs and ai_generation_logs_store_obj is not None:
        issues.append(
            _make_issue(
                "MYSQL_AI_GENERATION_LOGS_EMPTY",
                "info",
                "MySQL ai_generation_logs 暂无数据（Step4 执行时会写入幻觉/环境日志）",
            )
        )

    store_objs = {
        "review_store_obj": review_store_obj,
        "action_library_store_obj": action_library_store_obj,
        "figma_state_store_obj": figma_state_store_obj,
        "diff_snapshot_store_obj": diff_snapshot_store_obj,
    }
    store_result = _check_mysql_store_tables(store_objs)
    issues.extend(store_result["issues"])

    issues.extend(
        _check_mysql_trace_consistency(mysql_reqs, mysql_intents, trace_links)
    )

    mapping = _load_mapping(tracker) if db_exists else {}
    issues.extend(
        _check_mysql_mapping_consistency(mysql_intents, mapping, db_exists, tracker)
    )

    tl_stats = _try_health_stats(trace_link_store_obj)
    cov_stats = _try_health_stats(coverage_store_obj)
    aig_stats = _try_health_stats(ai_generation_logs_store_obj)
    req_stats = _try_health_stats(requirement_store_obj)
    intent_stats = _try_health_stats(case_store_obj)

    stats = {
        "mysql_requirement_count": int(
            req_stats.get("requirement_count", len(mysql_reqs))
        ),
        "mysql_intent_count": int(intent_stats.get("intent_count", len(mysql_intents))),
        "mysql_trace_link_count": len(trace_links),
        "mysql_coverage_result_count": len(cov_matrix) if cov_matrix else 0,
        "mysql_ai_generation_log_count": len(ai_generation_logs),
        **store_result["stats"],
        "trace_link_health": tl_stats,
        "coverage_health": cov_stats,
        "ai_generation_log_health": aig_stats,
        "requirement_health": req_stats,
        "test_case_health": intent_stats,
        "excel_requirement_count": len(excel_reqs),
        "excel_intent_count": len(excel_cases),
        "mapping_count": len(mapping),
        "excel_import_source_ok": excel_fallback_ok,
        "error_count": _count_severity(issues, "error"),
        "warning_count": _count_severity(issues, "warning"),
        "info_count": _count_severity(issues, "info"),
    }
    return {
        "ok": stats["error_count"] == 0,
        "issues": issues,
        "stats": stats,
    }
