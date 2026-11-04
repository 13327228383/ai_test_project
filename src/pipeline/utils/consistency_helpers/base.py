# -*- coding: utf-8 -*-
"""
基础工具函数
"""

import json
import re
from typing import Any, Set
import logging

_log = logging.getLogger(__name__)

Issue = dict[str, Any]


def _is_true(value: Any) -> bool:
    """兼容 Excel 中 bool / str / number 形态的真值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "是", "已生成")
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _parse_steps(value: Any) -> list[Any]:
    """尽量解析步骤字段；解析失败时返回空列表。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    text = str(value).strip()
    if not text:
        return []
    if "[step_checks]" in text:
        text = text.split("[step_checks]", 1)[0].strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    except Exception as _e:
        _log.debug("[base] _parse_steps 异常: %s", _e, exc_info=True)
    return [line for line in text.splitlines() if line.strip()]


def _load_mapping(tracker: Any) -> dict[str, str]:
    """从 tracker 读取映射，兼容测试替身和 IntentRequirementTracker。"""
    if hasattr(tracker, "_load_map"):
        return dict(tracker._load_map())
    if hasattr(tracker, "mapping"):
        return dict(tracker.mapping)
    return {}


def _infer_req_id_from_case_id(case_id: str, valid_req_ids: Set[str]) -> dict[str, Any]:
    """从 TC-XXX-N 弱推 REQ-XXX。仅用于生成修复建议，不直接写库。"""
    cid = str(case_id or "").strip()
    m = re.match(r"^TC-(\d{3})(?:-\d+)?$", cid)
    if not m:
        return {
            "req_id": "",
            "confidence": "none",
            "reason": "case_id 不符合 TC-XXX-N 约定",
        }
    rid = "REQ-%s" % m.group(1)
    if rid in valid_req_ids:
        return {
            "req_id": rid,
            "confidence": "medium",
            "reason": "由 case_id 约定弱推，且需求存在",
        }
    return {
        "req_id": rid,
        "confidence": "low",
        "reason": "由 case_id 约定弱推，但需求表不存在该 req_id",
    }


def _get_explicit_intent_req_id(intent: dict[str, Any]) -> str:
    """读取 Excel 测试用例中的显式 req_id。"""
    for key in ("req_id", "requirement_id", "需求编号"):
        value = str(intent.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _find_duplicate_case_ids(case_ids: list[str]) -> Set[str]:
    """从有序 case_id 列表中找出重复项。"""
    seen: Set[str] = set()
    duplicates: Set[str] = set()
    for cid in case_ids:
        if cid in seen:
            duplicates.add(cid)
        seen.add(cid)
    return duplicates


def _make_issue(code: str, severity: str, message: str, items: Any = None) -> Issue:
    """构造标准 Issue 字典，减少重复 dict 字面量。"""
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "items": items or [],
    }


def _safe_get_case_id(intent: dict[str, Any]) -> str:
    """从测试用例字典中安全提取 case_id 字符串。
    向后兼容：同时接受 case_id（新格式）和 case_id（旧格式）。
    """
    return str(intent.get("case_id") or intent.get("case_id") or "").strip()


def _safe_get_req_id(req_or_intent: dict[str, Any]) -> str:
    """从需求或测试用例字典中安全提取 req_id 字符串。"""
    return str(req_or_intent.get("req_id", "")).strip()


def _count_severity(issues: list[Issue], severity: str) -> int:
    """统计指定严重级别的 issue 数量。"""
    return sum(1 for i in issues if i.get("severity") == severity)


def _extract_case_ids(intents: list[dict[str, Any]]) -> list[str]:
    """从测试用例列表中提取非空 case_id 列表。"""
    return [cid for c in intents if (cid := _safe_get_case_id(c))]


def _extract_req_ids(reqs: list[dict[str, Any]]) -> Set[str]:
    """从需求列表中提取非空 req_id 集合。"""
    return {rid for r in reqs if (rid := _safe_get_req_id(r))}
