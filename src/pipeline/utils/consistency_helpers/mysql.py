# -*- coding: utf-8 -*-
from __future__ import annotations

"""
MySQL 迁移健康子检查
"""

from typing import Any

from .base import (
    Issue,
    _extract_req_ids,
    _make_issue,
    _safe_get_case_id,
    _safe_get_req_id,
)
from .store import _try_health_stats


def _check_store_empty(
    store_obj: Any | None,
    stats: dict[str, Any],
    stat_key: str,
    issue_code: str,
    severity: str,
    message: str,
) -> list[Issue]:
    """检查 store 的某个统计值是否为 0，为 0 则生成 issue。"""
    if store_obj is None:
        return []
    if int(stats.get(stat_key, 0)) == 0:
        return [_make_issue(issue_code, severity, message)]
    return []


def _check_mysql_store_tables(
    store_objs: dict[str, Any],
) -> dict[str, Any]:
    """检查各 MySQL store 表的健康状态，返回 {issues, stats}。"""
    issues: list[Issue] = []
    stats_summary: dict[str, Any] = {}

    # reviews
    review_store_obj = store_objs.get("review_store_obj")
    review_stats = _try_health_stats(review_store_obj)
    issues.extend(
        _check_store_empty(
            review_store_obj,
            review_stats,
            "case_meta_count",
            "MYSQL_INTENT_META_EMPTY",
            "info",
            "MySQL case_meta 暂无数据（Step3 生成测试用例后会写入置信度/风险等级）",
        )
    )
    issues.extend(
        _check_store_empty(
            review_store_obj,
            review_stats,
            "review_log_count",
            "MYSQL_REVIEW_LOG_EMPTY",
            "info",
            "MySQL review_log 暂无数据（人工审核/确认后会写入操作日志）",
        )
    )
    stats_summary["mysql_case_meta_count"] = int(review_stats.get("case_meta_count", 0))
    stats_summary["mysql_review_log_count"] = int(
        review_stats.get("review_log_count", 0)
    )
    stats_summary["mysql_approved_intent_count"] = int(
        review_stats.get("approved_intents", 0)
    )

    # action_library
    action_store_obj = store_objs.get("action_library_store_obj")
    action_stats = _try_health_stats(action_store_obj)
    issues.extend(
        _check_store_empty(
            action_store_obj,
            action_stats,
            "category_count",
            "MYSQL_ACTION_CATEGORIES_EMPTY",
            "info",
            "MySQL action_categories 暂无数据（Step4 浏览器 AI 需要动作白名单）",
        )
    )
    issues.extend(
        _check_store_empty(
            action_store_obj,
            action_stats,
            "action_count",
            "MYSQL_ACTION_ACTIONS_EMPTY",
            "info",
            "MySQL action_actions 暂无数据（首次加载会自动填充默认指令库）",
        )
    )
    stats_summary["mysql_action_category_count"] = int(
        action_stats.get("category_count", 0)
    )
    stats_summary["mysql_action_action_count"] = int(
        action_stats.get("action_count", 0)
    )

    # figma_state
    figma_store_obj = store_objs.get("figma_state_store_obj")
    figma_stats = _try_health_stats(figma_store_obj)
    issues.extend(
        _check_store_empty(
            figma_store_obj,
            figma_stats,
            "figma_api_quota",
            "MYSQL_FIGMA_API_QUOTA_EMPTY",
            "info",
            "MySQL figma_api_quota 暂无数据（Step1 拉取后会写入配额/限流状态）",
        )
    )
    issues.extend(
        _check_store_empty(
            figma_store_obj,
            figma_stats,
            "figma_change_sessions",
            "MYSQL_FIGMA_CHANGE_SESSIONS_EMPTY",
            "info",
            "MySQL figma_change_sessions 暂无数据（Step1 每次拉取后写入变更会话）",
        )
    )
    issues.extend(
        _check_store_empty(
            figma_store_obj,
            figma_stats,
            "figma_node_hashes",
            "MYSQL_FIGMA_NODE_HASHES_EMPTY",
            "info",
            "MySQL figma_node_hashes 暂无数据（Step1 节点级哈希用于变更检测）",
        )
    )
    stats_summary["mysql_figma_api_quota_count"] = int(
        figma_stats.get("figma_api_quota", 0)
    )
    stats_summary["mysql_figma_change_sessions_count"] = int(
        figma_stats.get("figma_change_sessions", 0)
    )
    stats_summary["mysql_figma_node_hashes_count"] = int(
        figma_stats.get("figma_node_hashes", 0)
    )

    # diff_snapshot_store
    diff_store_obj = store_objs.get("diff_snapshot_store_obj")
    diff_stats = _try_health_stats(diff_store_obj)
    issues.extend(
        _check_store_empty(
            diff_store_obj,
            diff_stats,
            "total",
            "MYSQL_DIFF_SNAPSHOTS_EMPTY",
            "info",
            "MySQL diff_snapshots 暂无数据（Step1/Step3 运行后会写入版本快照）",
        )
    )
    stats_summary["mysql_diff_snapshots_count"] = int(diff_stats.get("total", 0))

    return {"issues": issues, "stats": stats_summary}


def _check_mysql_trace_consistency(
    mysql_reqs: list[dict[str, Any]],
    mysql_intents: list[dict[str, Any]],
    trace_links: dict[str, Any],
) -> list[Issue]:
    """检查追溯链路一致性。"""
    issues: list[Issue] = []
    req_ids = _extract_req_ids(mysql_reqs)
    intent_req_ids = {_safe_get_req_id(c) for c in mysql_intents if _safe_get_req_id(c)}
    trace_req_ids = {x for rid in trace_links if (x := str(rid).strip())}

    if req_ids and trace_req_ids - req_ids:
        issues.append(
            _make_issue(
                "MYSQL_TRACE_REQ_TARGET_MISSING",
                "warning",
                "trace_links 中的 req_id 在 requirements 表中找不到",
                sorted(trace_req_ids - req_ids)[:50],
            )
        )
    missing_req_targets = sorted(intent_req_ids - req_ids)
    if missing_req_targets:
        issues.append(
            _make_issue(
                "MYSQL_INTENT_REQ_TARGET_MISSING",
                "error",
                "test_cases.req_id 在 requirements 表中不存在",
                missing_req_targets,
            )
        )
    intents_without_req_id = [
        _safe_get_case_id(c)
        for c in mysql_intents
        if _safe_get_case_id(c) and not _safe_get_req_id(c)
    ]
    if intents_without_req_id:
        issues.append(
            _make_issue(
                "MYSQL_INTENT_WITHOUT_REQ_ID",
                "warning",
                "MySQL test_cases 中存在未携带 req_id 的测试用例",
                intents_without_req_id,
            )
        )
    return issues


def _check_mysql_mapping_consistency(
    mysql_intents: list[dict[str, Any]],
    mapping: dict[str, str],
    db_exists: bool,
    tracker: Any,
) -> list[Issue]:
    """检查 MySQL test_cases 与 case_requirement_mapping 的一致性。"""
    issues: list[Issue] = []
    if db_exists:
        mapping_error = getattr(tracker, "_last_error", "")
        if mapping_error:
            issues.append(
                _make_issue(
                    "MYSQL_MAPPING_READ_FAILED",
                    "warning",
                    "case_requirement_mapping 读取失败或尚未初始化",
                    [str(mapping_error)[:200]],
                )
            )
    else:
        mapping = {}
    mapped_case_ids = {x for k in mapping if (x := str(k).strip())}
    mysql_case_ids = {
        _safe_get_case_id(c) for c in mysql_intents if _safe_get_case_id(c)
    }
    missing_mapping = sorted(mysql_case_ids - mapped_case_ids)
    if missing_mapping:
        issues.append(
            _make_issue(
                "MYSQL_INTENT_MAPPING_MISSING",
                "warning",
                "MySQL test_cases 中部分测试用例缺少 case_requirement_mapping",
                missing_mapping,
            )
        )
    return issues
