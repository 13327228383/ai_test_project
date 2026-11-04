# -*- coding: utf-8 -*-
"""Pipeline Step 数据契约校验。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_ALLOWED_STATUS = {"completed", "success", "failed", "error", "paused", "skipped"}


def validate_step_result(step_num: int, result: Any) -> dict[str, Any]:
    """校验并规范化 Step 返回值。"""
    if result is None:
        normalized: dict[str, Any] = {"success": True, "status": "completed"}
    elif not isinstance(result, dict):
        return _contract_failed(
            step_num, ["Step 返回值必须是 dict 或 None"], raw_type=type(result).__name__
        )
    else:
        normalized = deepcopy(result)

    errors: list[str] = []
    success = normalized.get("success", True)
    if not isinstance(success, bool):
        errors.append("success 字段必须是 bool")
    if "count" in normalized and not isinstance(normalized.get("count"), int):
        errors.append("count 字段必须是 int")
    if "warnings" in normalized and not isinstance(normalized.get("warnings"), list):
        errors.append("warnings 字段必须是 list")
    if "stats" in normalized:
        stats = normalized.get("stats")
        if not isinstance(stats, dict):
            errors.append("stats 字段必须是 dict")
        else:
            for key in ("passed", "failed", "total"):
                if key in stats and not isinstance(stats.get(key), int):
                    errors.append("stats.%s 字段必须是 int" % key)
    status = normalized.get("status")
    if status is not None and status not in _ALLOWED_STATUS:
        errors.append("status 字段非法: %s" % status)
    if errors:
        failed = _contract_failed(step_num, errors)
        failed["raw_result"] = normalized
        return failed
    normalized["success"] = bool(success)
    if "status" not in normalized:
        if normalized.get("paused"):
            normalized["status"] = "paused"
        elif normalized["success"]:
            normalized["status"] = "completed"
        else:
            normalized["status"] = "failed"
    return normalized


def _contract_failed(
    step_num: int, errors: list[str], raw_type: str = ""
) -> dict[str, Any]:
    message = "Step %d 返回值不符合数据契约: %s" % (step_num, "；".join(errors))
    if raw_type:
        message += "；raw_type=%s" % raw_type
    return {
        "success": False,
        "status": "failed",
        "error": message,
        "contract_errors": errors,
    }
