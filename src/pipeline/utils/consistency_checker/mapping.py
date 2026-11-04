# -*- coding: utf-8 -*-
from __future__ import annotations

"""
测试用例映射一致性检查
====================
检查 Excel 测试用例、需求 Excel 与 MySQL intent→req 映射的一致性。
"""

from typing import Any, Set
from collections import Counter

from src.pipeline.utils.consistency_helpers import (
    Issue,
    _is_true,
    _parse_steps,
    _load_mapping,
    _infer_req_id_from_case_id,
    _get_explicit_intent_req_id,
    _find_duplicate_case_ids,
    _make_issue,
    _safe_get_case_id,
    _safe_get_req_id,
    _count_severity,
    _extract_case_ids,
    _extract_req_ids,
)


def check_intent_mapping_consistency(
    excel_handler: Any | None = None,
    tracker: Any | None = None,
    min_intents_per_generated_req: int = 1,
) -> dict[str, Any]:
    """检查 Excel 测试用例、需求 Excel 与 MySQL intent→req 映射的一致性。"""
    if excel_handler is None:
        from src.core.excel_handler import excel_handler as excel_handler
    if tracker is None:
        from src.pipeline.utils.case_requirement_tracker import (
            case_requirement_tracker as tracker,
        )

    issues: list[Issue] = []
    intents = excel_handler.read_test_cases()
    reqs = excel_handler.read_requirements()
    mapping = _load_mapping(tracker)
    mapping_error = getattr(tracker, "_last_error", "")
    if mapping_error:
        issues.append(
            _make_issue(
                "MAPPING_READ_FAILED",
                "error",
                "MySQL intent→req 映射读取失败，无法判断 Excel/MySQL 是否一致",
                [str(mapping_error)[:200]],
            )
        )

    case_ids = _extract_case_ids(intents)
    case_id_set: Set[str] = set(case_ids)
    req_ids = _extract_req_ids(reqs)

    # 重复 case_id
    duplicate_case_ids = _find_duplicate_case_ids(case_ids)
    if duplicate_case_ids:
        issues.append(
            _make_issue(
                "DUPLICATE_INTENT_ID",
                "error",
                "Excel 中存在重复 case_id",
                sorted(duplicate_case_ids),
            )
        )

    # Excel 测试用例中无效的 req_id
    invalid_excel_req_ids = []
    for intent in intents:
        cid = _safe_get_case_id(intent)
        explicit_rid = _get_explicit_intent_req_id(intent)
        if explicit_rid and explicit_rid not in req_ids:
            invalid_excel_req_ids.append({"case_id": cid, "req_id": explicit_rid})
    if invalid_excel_req_ids:
        issues.append(
            _make_issue(
                "INVALID_EXCEL_REQ_ID",
                "error",
                "Excel 测试用例中的 req_id 不存在于需求表",
                invalid_excel_req_ids,
            )
        )

    # 缺少 MySQL 映射
    missing_mapping = [cid for cid in case_ids if not mapping.get(cid)]
    if missing_mapping:
        issues.append(
            _make_issue(
                "MISSING_INTENT_MAPPING",
                "error",
                "Excel 测试用例缺少 MySQL intent→req 映射",
                missing_mapping,
            )
        )

    # 过期映射
    stale_mapping = [cid for cid in mapping.keys() if cid not in case_id_set]
    if stale_mapping:
        issues.append(
            _make_issue(
                "STALE_INTENT_MAPPING",
                "warning",
                "MySQL 映射中存在 Excel 已不存在的 case_id",
                sorted(stale_mapping),
            )
        )

    # 映射指向不存在的 req_id
    invalid_req_mapping = [
        {"case_id": cid, "req_id": rid}
        for cid, rid in mapping.items()
        if rid and rid not in req_ids
    ]
    if invalid_req_mapping:
        issues.append(
            _make_issue(
                "INVALID_REQ_MAPPING",
                "error",
                "MySQL 映射指向不存在的 req_id",
                invalid_req_mapping,
            )
        )

    # Excel 与 MySQL 映射不一致
    mapping_mismatch = []
    for intent in intents:
        cid = _safe_get_case_id(intent)
        explicit_rid = _get_explicit_intent_req_id(intent)
        mapped_rid = mapping.get(cid, "")
        if cid and explicit_rid and mapped_rid and explicit_rid != mapped_rid:
            mapping_mismatch.append(
                {
                    "case_id": cid,
                    "excel_req_id": explicit_rid,
                    "mysql_req_id": mapped_rid,
                }
            )
    if mapping_mismatch:
        issues.append(
            _make_issue(
                "INTENT_MAPPING_MISMATCH",
                "error",
                "Excel 测试用例 req_id 与 MySQL 映射不一致",
                mapping_mismatch,
            )
        )

    # 已标记生成但测试用例不足的需求
    intent_count_by_req: dict[str, int] = Counter()
    for intent in intents:
        cid = _safe_get_case_id(intent)
        rid = mapping.get(cid, "") or _get_explicit_intent_req_id(intent)
        if rid:
            intent_count_by_req[rid] += 1

    generated_without_intents = []
    for req in reqs:
        rid = _safe_get_req_id(req)
        if not rid:
            continue
        if (
            _is_true(req.get("is_generated"))
            and intent_count_by_req.get(rid, 0) < min_intents_per_generated_req
        ):
            generated_without_intents.append(rid)
    if generated_without_intents:
        issues.append(
            _make_issue(
                "GENERATED_REQ_WITHOUT_INTENTS",
                "error",
                "需求已标记生成，但映射到该需求的测试用例数量不足",
                generated_without_intents,
            )
        )

    return {
        "ok": not any(i.get("severity") == "error" for i in issues),
        "issues": issues,
        "stats": {
            "intent_count": len(case_ids),
            "req_count": len(req_ids),
            "mapping_count": len(mapping),
            "error_count": _count_severity(issues, "error"),
            "warning_count": _count_severity(issues, "warning"),
        },
    }


def build_intent_mapping_repair_plan(
    excel_handler: Any | None = None,
    tracker: Any | None = None,
) -> dict[str, Any]:
    """生成 Excel/MySQL 映射修复计划。只读，不修改 MySQL。"""
    if excel_handler is None:
        from src.core.excel_handler import excel_handler as excel_handler
    if tracker is None:
        from src.pipeline.utils.case_requirement_tracker import (
            case_requirement_tracker as tracker,
        )

    intents = excel_handler.read_test_cases()
    reqs = excel_handler.read_requirements()
    mapping = _load_mapping(tracker)
    mapping_error = getattr(tracker, "_last_error", "")

    req_ids = _extract_req_ids(reqs)
    case_ids = _extract_case_ids(intents)
    case_id_set = set(case_ids)

    plan: dict[str, Any] = {
        "ok_to_apply_automatically": False,
        "blocked": False,
        "block_reason": "",
        "actions": [],
        "stats": {
            "intent_count": len(case_ids),
            "req_count": len(req_ids),
            "mapping_count": len(mapping),
            "safe_action_count": 0,
            "review_action_count": 0,
        },
    }

    if mapping_error:
        plan["blocked"] = True
        plan["block_reason"] = (
            "映射读取失败，不能生成可靠修复计划: %s" % str(mapping_error)[:200]
        )
        return plan

    # 重复 case_id
    duplicate_case_ids = _find_duplicate_case_ids(case_ids)
    for cid in sorted(duplicate_case_ids):
        plan["actions"].append(
            {
                "action": "manual_review",
                "case_id": cid,
                "severity": "error",
                "reason": "Excel 中存在重复 case_id，无法安全修复映射",
            }
        )

    # 过期映射
    for cid in sorted(mapping.keys()):
        if cid not in case_id_set:
            plan["actions"].append(
                {
                    "action": "remove_stale_mapping",
                    "case_id": cid,
                    "req_id": mapping.get(cid, ""),
                    "severity": "warning",
                    "safe": True,
                    "reason": "MySQL 映射存在，但 Excel 测试用例已不存在",
                }
            )

    # 缺失映射
    for cid in case_ids:
        rid = mapping.get(cid, "")
        if not rid:
            intent = next((c for c in intents if _safe_get_case_id(c) == cid), {})
            explicit_rid = _get_explicit_intent_req_id(intent)
            if explicit_rid and explicit_rid in req_ids:
                plan["actions"].append(
                    {
                        "action": "add_missing_mapping",
                        "case_id": cid,
                        "req_id": explicit_rid,
                        "severity": "warning",
                        "safe": True,
                        "confidence": "high",
                        "reason": "Excel 测试用例显式 req_id 存在且需求表中存在",
                    }
                )
                continue
            if explicit_rid and explicit_rid not in req_ids:
                plan["actions"].append(
                    {
                        "action": "manual_review",
                        "case_id": cid,
                        "req_id": explicit_rid,
                        "severity": "error",
                        "reason": "Excel 测试用例显式 req_id 不存在于需求表",
                    }
                )
                continue

            inferred = _infer_req_id_from_case_id(cid, req_ids)
            if inferred.get("req_id") and inferred.get("req_id") in req_ids:
                plan["actions"].append(
                    {
                        "action": "add_missing_mapping",
                        "case_id": cid,
                        "req_id": inferred["req_id"],
                        "severity": "warning",
                        "safe": False,
                        "confidence": inferred["confidence"],
                        "reason": inferred["reason"],
                    }
                )
            else:
                plan["actions"].append(
                    {
                        "action": "manual_review",
                        "case_id": cid,
                        "severity": "error",
                        "reason": inferred.get("reason") or "缺少映射且无法推断 req_id",
                    }
                )
        elif rid not in req_ids:
            plan["actions"].append(
                {
                    "action": "manual_review",
                    "case_id": cid,
                    "req_id": rid,
                    "severity": "error",
                    "reason": "映射指向不存在的 req_id",
                }
            )

    safe_count = sum(1 for a in plan["actions"] if a.get("safe"))
    review_count = len(plan["actions"]) - safe_count
    plan["stats"]["safe_action_count"] = safe_count
    plan["stats"]["review_action_count"] = review_count
    plan["ok_to_apply_automatically"] = bool(plan["actions"]) and review_count == 0
    return plan
