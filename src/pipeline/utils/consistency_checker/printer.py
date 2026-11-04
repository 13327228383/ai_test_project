# -*- coding: utf-8 -*-
"""
一致性检查结果打印模块
====================
把检查结果打印成短日志。只打印，不抛异常。
"""

from typing import Any
import logging

_log = logging.getLogger(__name__)

from src.pipeline.utils.consistency_helpers import (
    _format_items_preview,
)


def print_check_report(stage: str, result: dict[str, Any], max_items: int = 5) -> None:
    """把检查结果打印成短日志。只打印，不抛异常。"""
    summary = result.get("summary", {})
    err = int(summary.get("error_count", 0) or 0)
    warn = int(summary.get("warning_count", 0) or 0)
    if err == 0 and warn == 0:
        _log.info("  [一致性检查:%s] 通过", stage)
        return

    _log.warning(
        "  [一致性检查:%s] 发现 %d 个错误、%d 个警告（当前仅提示，不阻断流程）"
        % (stage, err, warn)
    )
    checks = result.get("checks", {})
    for check_name, check_result in checks.items():
        for issue in check_result.get("issues", []):
            code = issue.get("code", "UNKNOWN")
            severity = issue.get("severity", "warning")
            message = issue.get("message", "")
            preview = _format_items_preview(issue.get("items", []), max_items)
            _log.info(
                "    - [%s] %s/%s: %s %s"
                % (severity.upper(), check_name, code, message, preview)
            )


def print_repair_plan(plan: dict[str, Any], max_items: int = 10) -> None:
    """打印修复计划摘要。只打印，不修改数据。"""
    if plan.get("blocked"):
        _log.info("  [修复计划] 无法生成: %s", plan.get("block_reason", "未知原因"))
        return
    actions = plan.get("actions", [])
    if not actions:
        _log.info("  [修复计划] 无需修复")
        return
    _log.info(
        "  [修复计划] 共 %d 项；可安全自动处理 %d 项；需人工确认 %d 项"
        % (
            len(actions),
            plan.get("stats", {}).get("safe_action_count", 0),
            plan.get("stats", {}).get("review_action_count", 0),
        )
    )
    for action in actions[:max_items]:
        _log.info(
            "    - %s intent=%s req=%s safe=%s reason=%s"
            % (
                action.get("action"),
                action.get("case_id", ""),
                action.get("req_id", ""),
                action.get("safe", False),
                action.get("reason", ""),
            )
        )
    if len(actions) > max_items:
        _log.info("    ... 其余 %d 项省略", (len(actions) - max_items))
