# -*- coding: utf-8 -*-
from __future__ import annotations

"""
修复操作
"""

from typing import Any

from .base import _get_explicit_intent_req_id
import logging

_log = logging.getLogger(__name__)


def apply_stale_mapping_repair(
    plan: dict[str, Any],
    tracker: Any | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """只处理 remove_stale_mapping。"""
    if tracker is None:
        from src.pipeline.utils.case_requirement_tracker import (
            case_requirement_tracker as tracker,
        )
    actions = [
        a for a in plan.get("actions", []) if a.get("action") == "remove_stale_mapping"
    ]
    result = {"dry_run": dry_run, "removed": [], "failed": [], "skipped": []}
    for action in actions:
        cid = action.get("case_id", "")
        if not cid:
            result["skipped"].append({"case_id": cid, "reason": "缺少 case_id"})
            continue
        if dry_run:
            result["removed"].append(cid)
            continue
        ok = tracker.remove_intent(cid)
        if ok:
            result["removed"].append(cid)
        else:
            result["failed"].append(cid)
    return result


def apply_safe_missing_mapping_repair(
    plan: dict[str, Any],
    tracker: Any | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """只补 Excel 显式 req_id 产生的安全缺失映射。"""
    if tracker is None:
        from src.pipeline.utils.case_requirement_tracker import (
            case_requirement_tracker as tracker,
        )
    actions = [
        a
        for a in plan.get("actions", [])
        if a.get("action") == "add_missing_mapping"
        and a.get("safe") == True
        and a.get("confidence") == "high"
    ]
    result = {"dry_run": dry_run, "added": [], "failed": [], "skipped": []}
    for action in actions:
        cid = action.get("case_id", "")
        rid = action.get("req_id", "")
        if not cid or not rid:
            result["skipped"].append(
                {"case_id": cid, "req_id": rid, "reason": "缺少 case_id 或 req_id"}
            )
            continue
        if dry_run:
            result["added"].append({"case_id": cid, "req_id": rid})
            continue
        ok = tracker.add(cid, rid)
        if ok:
            result["added"].append({"case_id": cid, "req_id": rid})
        else:
            result["failed"].append({"case_id": cid, "req_id": rid})
    return result


def reset_user_db(confirm: bool = False, backup: bool = False) -> dict[str, Any]:
    """[已废弃] MySQL 模式下 data.db 不存在，此命令为空操作。

    保留签名兼容 CLI --reset-user-db，但不再执行任何文件操作（全走 MySQL）。
    """
    return {
        "confirmed": confirm,
        "backup": backup,
        "backup_dir": "",
        "moved": [],
        "deleted": [],
        "planned": [],
        "deprecated": True,
        "note": "MySQL 模式下 data.db 不存在，此命令已废弃（空操作）",
    }


def sync_excel_to_mysql(
    excel_handler: Any | None = None,
    tracker: Any | None = None,
    requirement_store_obj: Any | None = None,
    case_store_obj: Any | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """把现有 Excel 需求/测试用例回填到 MySQL 镜像。"""
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

    reqs = excel_handler.read_requirements()
    intents = excel_handler.read_test_cases()
    case_asset_file = ""
    try:
        case_asset_path = getattr(excel_handler, "_case_asset_path", None) or getattr(
            excel_handler, "_case_path", None
        )
        case_asset_file = case_asset_path.name if case_asset_path else ""
    except Exception as _e:
        _log.debug("[repair] sync_excel_to_mysql 异常: %s", _e, exc_info=True)
        case_asset_file = ""

    explicit_mappings = []
    for intent in intents:
        cid = str(intent.get("case_id", "") or "").strip()
        rid = _get_explicit_intent_req_id(intent)
        if cid and rid:
            explicit_mappings.append({"case_id": cid, "req_id": rid})

    result = {
        "dry_run": dry_run,
        "requirements": len(reqs),
        "test_cases": len(intents),
        "case_asset_file": case_asset_file,
        "explicit_mappings": len(explicit_mappings),
        "written": {"requirements": 0, "test_cases": 0, "case_requirement_mapping": 0},
        "failed": [],
    }
    if dry_run:
        return result
    if reqs:
        if requirement_store_obj.upsert_requirements(reqs):
            result["written"]["requirements"] = len(reqs)
        else:
            result["failed"].append("requirements")
    if intents:
        if case_store_obj.upsert_intents(intents, case_asset_file=case_asset_file):
            result["written"]["test_cases"] = len(intents)
        else:
            result["failed"].append("test_cases")
    for item in explicit_mappings:
        try:
            if tracker.add(item["case_id"], item["req_id"]):
                result["written"]["case_requirement_mapping"] += 1
            else:
                result["failed"].append("mapping:%s" % item["case_id"])
        except Exception as e:
            result["failed"].append("mapping:%s:%s" % (item["case_id"], str(e)[:80]))
    return result
