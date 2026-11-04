# -*- coding: utf-8 -*-
"""
打印辅助
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


def _format_items_preview(items: Any, max_items: int) -> str:
    """格式化 items 列表为预览字符串。"""
    if isinstance(items, list):
        preview = items[:max_items]
        more = "" if len(items) <= max_items else " ... 共%d项" % len(items)
        return "%s%s" % (preview, more)
    return str(items)


def _print_empty_table_hints(
    req_stats: dict[str, Any], intent_stats: dict[str, Any]
) -> None:
    """打印空表提示。"""
    hints = []
    if (
        isinstance(req_stats, dict)
        and int(req_stats.get("requirement_count") or 0) == 0
    ):
        hints.append("requirements 表为空（尚未导入需求，Step2/Step3 会缺少主事实源）")
    if (
        isinstance(intent_stats, dict)
        and int(intent_stats.get("intent_count") or 0) == 0
    ):
        hints.append("test_cases 表为空（Step3 尚未生成测试用例或尚未写入 MySQL 镜像）")
    if hints:
        _log.info("  [MySQL迁移健康·信息] 空表提示:")
        for h in hints:
            _log.info("    - %s", h)


def print_mysql_health_report(result: dict[str, Any], max_items: int = 8) -> None:
    """打印 MySQL 迁移健康检查摘要。"""
    stats = result.get("stats", {})
    _log.info(
        "  [MySQL迁移健康] requirements=%d, test_cases=%d, trace_links=%d, "
        "coverage_results=%d, ai_generation_logs=%d, case_meta=%d, review_log=%d, "
        "approved_intents=%d, action_categories=%d, action_actions=%d, "
        "figma_api_quota=%d, figma_change_sessions=%d, figma_node_hashes=%d, "
        "diff_snapshots=%d, mapping=%d, Excel导入源=%s"
        % (
            stats.get("mysql_requirement_count", 0),
            stats.get("mysql_intent_count", 0),
            stats.get("mysql_trace_link_count", 0),
            stats.get("mysql_coverage_result_count", 0),
            stats.get("mysql_ai_generation_log_count", 0),
            stats.get("mysql_case_meta_count", 0),
            stats.get("mysql_review_log_count", 0),
            stats.get("mysql_approved_intent_count", 0),
            stats.get("mysql_action_category_count", 0),
            stats.get("mysql_action_action_count", 0),
            stats.get("mysql_figma_api_quota_count", 0),
            stats.get("mysql_figma_change_sessions_count", 0),
            stats.get("mysql_figma_node_hashes_count", 0),
            stats.get("mysql_diff_snapshots_count", 0),
            stats.get("mapping_count", 0),
            "可用" if stats.get("excel_import_source_ok") else "不可用",
        )
    )

    _tl = stats.get("trace_link_health") or {}
    _cov = stats.get("coverage_health") or {}
    _aig = stats.get("ai_generation_log_health") or {}
    _req = stats.get("requirement_health") or {}
    _intent = stats.get("test_case_health") or {}

    if isinstance(_tl, dict) and _tl.get("trace_link_count"):
        _log.info(
            "  [MySQL迁移健康] trace_links_health: total=%d, high=%d, medium=%d, low=%d, with_api=%d"
            % (
                int(_tl.get("trace_link_count") or 0),
                int(_tl.get("high_confidence") or 0),
                int(_tl.get("medium_confidence") or 0),
                int(_tl.get("low_confidence") or 0),
                int(_tl.get("has_api_endpoints") or 0),
            )
        )
    if isinstance(_cov, dict) and _cov.get(
        "coverage_summary_count", _cov.get("coverage_result_count")
    ):
        _log.info(
            "  [MySQL迁移健康] coverage_health: total_requirements=%d, total_cases=%d, coverage_rate=%.2f%%, gaps=%d"
            % (
                int(_cov.get("total_requirements") or 0),
                int(_cov.get("total_cases") or 0),
                float(_cov.get("coverage_rate") or 0.0),
                int(_cov.get("requirements_with_gaps") or 0),
            )
        )
    if isinstance(_aig, dict) and _aig.get("log_count"):
        _log.info(
            "  [MySQL迁移健康] ai_generation_log_health: total=%d, hallucination=%d, env=%d, unknown=%d, distinct_intents=%d"
            % (
                int(_aig.get("log_count") or 0),
                int(_aig.get("hallucination_count") or 0),
                int(_aig.get("env_count") or 0),
                int(_aig.get("unknown_count") or 0),
                int(_aig.get("distinct_case_ids") or 0),
            )
        )
    if isinstance(_req, dict) and int(_req.get("requirement_count") or 0) > 0:
        _log.info(
            "  [MySQL迁移健康] requirement_health: total=%d, generated=%d, pending=%d, approved=%d, modules=%d"
            % (
                int(_req.get("requirement_count") or 0),
                int(_req.get("generated_count") or 0),
                int(_req.get("pending_count") or 0),
                int(_req.get("approved_count") or 0),
                int(_req.get("module_count") or 0),
            )
        )
    if isinstance(_intent, dict) and int(_intent.get("intent_count") or 0) > 0:
        _log.info(
            "  [MySQL迁移健康] test_case_health: total=%d, with_req_id=%d, distinct_req_ids=%d, "
            "high_confidence=%d, approved=%d, pending_review=%d"
            % (
                int(_intent.get("intent_count") or 0),
                int(_intent.get("intents_with_req_id") or 0),
                int(_intent.get("distinct_req_ids") or 0),
                int(_intent.get("high_confidence_intents") or 0),
                int(_intent.get("approved_intents") or 0),
                int(_intent.get("pending_review_intents") or 0),
            )
        )

    _print_empty_table_hints(_req, _intent)

    issues = result.get("issues", [])
    if not issues:
        _log.info("  [MySQL迁移健康] 通过")
        return
    error_count = stats.get("error_count", 0)
    warning_count = stats.get("warning_count", 0)
    info_count = stats.get("info_count", 0)
    _log.error(
        "  [MySQL迁移健康] 发现 %d 个错误、%d 个警告、%d 条信息",
        error_count,
        warning_count,
        info_count,
    )
    for issue in issues:
        items_text = _format_items_preview(issue.get("items", []), max_items)
        _log.warning(
            "    - [%s] %s: %s %s"
            % (
                str(issue.get("severity", "warning")).upper(),
                issue.get("code", "UNKNOWN"),
                issue.get("message", ""),
                items_text,
            )
        )
