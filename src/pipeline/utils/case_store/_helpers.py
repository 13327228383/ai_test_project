# -*- coding: utf-8 -*-
"""
测试用例存储模块内部工具函数
"""

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)


def _to_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception as e:
        _log.warning("[Store] _to_json 序列化失败, 回退 str(): %s", e)
        return json.dumps(str(value), ensure_ascii=False)


def _row_to_intent(row: dict[str, Any]) -> dict[str, Any]:
    """把 test_cases 行还原为前端可用的 intent dict。"""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    intent = dict(payload)
    intent["payload"] = payload
    # 基础字段从 row 回填（payload 优先，row 兜底）
    for key in (
        "case_id",
        "version_id",
        "origin_case_id",
        "origin_version_id",
        "case_source",
        "valid_status",
        "req_id",
        "case_asset_file",
        "title",
        "case_type",
        "risk_level",
        "case_actions_json",
        "expected_outcome",
        "payload_json",
        "test_method",
        "control_type",
        "precondition",
        "test_steps",
        "expected_result",
        "created_at",
        "updated_at",
    ):
        value = row.get(key, "")
        if value not in (None, "") and not intent.get(key):
            intent[key] = value
    if row.get("_active_req_id"):
        intent["req_id"] = row.get("_active_req_id")
    # v9: 意图摘要直接来自 title 字段
    what = str(intent.get("title") or "")
    intent["intent_summary"] = (
        what[:120] if what else str(intent.get("title") or "")[:120]
    )
    return intent
