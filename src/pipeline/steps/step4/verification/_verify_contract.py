# -*- coding: utf-8 -*-
"""契约校验器（W3 T04，L0-4）—— 手写轻量 JSON Schema 子集，零新依赖。

check 格式：
  {"type": "api_contract", "url_pattern": "...",
   "required_fields": ["data", "code"],
   "types": {"data": "object", "code": "number"},
   "enums": {"status": ["active", "inactive"]},
   "formats": {"email": "email", "created_at": "date-time"},
   "status_ok": [0, 200]}

从 ApiTracker 最近匹配请求取 resp_body → 校验必填字段存在 + 类型 + 枚举 + 格式 + 业务码。
★ fail-closed：无匹配请求 / 响应体为空 → 判 FAIL（不允许假通过）。

★ W3 T04 关键改动 [已复核-DeepSeek-V4-Flash 2026-08-05]（2026-08-05）：
  契约校验器（零新依赖手写轻量 JSON Schema 子集）；CONTRACT_ENABLED 门控。

★ 2026-08-12 增强：新增枚举值校验（_check_enums）和格式校验（_check_formats），
  覆盖 email/uri/date-time/date/ipv4/ipv6/uuid 格式。与 contract_builder 生成的
  enums/formats 字段对齐，实现完整 JSON Schema 验证端到端闭环。
"""
from __future__ import annotations

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

# 格式校验正则表（email/uri/date-time/date/ipv4/ipv6/uuid）
_FORMAT_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "uri": re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s]+$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "date-time": re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"),
    "ipv4": re.compile(r"^\d{1,3}(\.\d{1,3}){3}$"),
    "ipv6": re.compile(r"^[0-9a-fA-F:]+$"),
    "uuid": re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    ),
}


def _type_name(value: Any) -> str:
    """把 Python 值映射为契约类型名（object/array/string/number/boolean/null）。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _find_by_url(tracker, url_pattern: str) -> list[dict]:
    """从 tracker 找最近匹配请求（含已响应但 body 可能为空的调用，供 fail-closed）。"""
    if tracker is None:
        return []
    calls = []
    try:
        if hasattr(tracker, "_recent_requests"):
            calls = list(tracker._recent_requests)
        elif hasattr(tracker, "requests"):
            calls = list(tracker.requests)
        elif hasattr(tracker, "_calls"):
            calls = list(tracker._calls)
    except Exception as e:  # noqa: BLE001
        _log.warning("[contract] 读取 tracker 失败: %s", e)
        return []
    # 仅匹配已收到响应的调用（has_response=True）；body 为空也保留，由上层 fail-closed
    if not url_pattern:
        return [c for c in calls if c.get("has_response")]
    return [
        c
        for c in calls
        if url_pattern in str(c.get("url", "")) and c.get("has_response")
    ]


def _check_required_fields(body: dict, required_fields: list) -> list[str]:
    missing = [f for f in required_fields or [] if f not in body]
    return missing


def _check_types(body: dict, types: dict) -> list[str]:
    bad = []
    for field, expected in (types or {}).items():
        if field not in body:
            continue
        actual = _type_name(body[field])
        # 兼容别名：integer→number / bool→boolean
        if expected in ("integer", "int"):
            expected = "number"
        if expected in ("bool",):
            expected = "boolean"
        if actual != expected:
            bad.append(f"{field}: 期望{expected} 实得{actual}")
    return bad


def _check_status_ok(body: dict, code_field: str, status_ok: list) -> bool:
    """业务码 ∈ status_ok（无 code_field 或 body 无该字段 → 视为通过，仅校验存在性）。"""
    if not code_field:
        return True
    if code_field not in body:
        return False
    val = body.get(code_field)
    if isinstance(val, bool):
        return val in status_ok
    try:
        return int(val) in status_ok
    except (ValueError, TypeError):
        return False


def _check_enums(body: dict, enums: dict) -> list[str]:
    """校验枚举值：字段值必须在允许枚举列表中。

    ★ 2026-08-12 新增：完整 JSON Schema 验证维度之一。
    """
    bad: list[str] = []
    for field, allowed in (enums or {}).items():
        if field not in body:
            continue
        val = body[field]
        if val not in allowed:
            bad.append(f"{field}: 值 {val!r} 不在枚举 {allowed} 中")
    return bad


def _check_formats(body: dict, formats: dict) -> list[str]:
    """校验格式（email/uri/date-time/date/ipv4/ipv6/uuid）。

    ★ 2026-08-12 新增：完整 JSON Schema 验证维度之一。
    字段值非 string 时直接判不符失败（format 仅适用于 string 类型）。
    """
    bad: list[str] = []
    for field, fmt in (formats or {}).items():
        if field not in body:
            continue
        val = body[field]
        if not isinstance(val, str):
            bad.append(f"{field}: 格式 {fmt} 要求 string，实得 {_type_name(val)}")
            continue
        pat = _FORMAT_PATTERNS.get(fmt)
        if pat and not pat.match(val):
            bad.append(f"{field}: 值 {val!r} 不符合 {fmt} 格式")
    return bad


async def verify_api_contract(
    api_tracker, check: dict, start_time: float = 0
) -> dict[str, Any]:
    """契约校验器。

    check: {"type":"api_contract","url_pattern":"...","required_fields":[...],
            "types":{...},"status_ok":[0,200]}
    取最近匹配请求的 resp_body → 校验必填字段存在 + 类型 + 业务码 ∈ status_ok。
    ★ fail-closed：无匹配请求/响应体为空 → 判 FAIL（不允许假通过）。
    """
    url_pattern = str(check.get("url_pattern", "") or "")
    required_fields = list(check.get("required_fields") or [])
    types = dict(check.get("types") or {})
    enums = dict(check.get("enums") or {})
    formats = dict(check.get("formats") or {})
    status_ok = list(check.get("status_ok") or [])
    code_field = str(check.get("code_field", "") or "").strip()

    matched = _find_by_url(api_tracker, url_pattern)
    if not matched:
        return {
            "passed": False,
            "status": "FAIL",
            "evidence": f"契约[api_contract] 未找到匹配请求（url={url_pattern or '*' }）→ fail-closed FAIL",
        }
    req = matched[-1]
    body = req.get("resp_body")
    if body is None:
        return {
            "passed": False,
            "status": "FAIL",
            "evidence": f"契约[api_contract] {req.get('url','')[:60]} 响应体为空 → fail-closed FAIL",
        }
    if not isinstance(body, dict):
        return {
            "passed": False,
            "status": "FAIL",
            "evidence": f"契约[api_contract] 响应体非 object（{_type_name(body)}）",
        }

    problems: list[str] = []
    missing = _check_required_fields(body, required_fields)
    if missing:
        problems.append("缺少必填字段: " + ", ".join(missing))
    type_bad = _check_types(body, types)
    if type_bad:
        problems.append("类型不符: " + "; ".join(type_bad))
    # ★ 2026-08-12: 枚举值校验
    enum_bad = _check_enums(body, enums)
    if enum_bad:
        problems.append("枚举不符: " + "; ".join(enum_bad))
    # ★ 2026-08-12: 格式校验（email/uri/date-time 等）
    format_bad = _check_formats(body, formats)
    if format_bad:
        problems.append("格式不符: " + "; ".join(format_bad))
    if not _check_status_ok(body, code_field, status_ok):
        problems.append(
            f"业务码不符: {code_field}={body.get(code_field)!r} ∉ {status_ok}"
        )

    passed = len(problems) == 0
    evidence = "契约[api_contract] %s %s" % (
        req.get("url", "")[:60],
        "通过" if passed else ("不通过: " + "; ".join(problems)),
    )
    return {"passed": passed, "evidence": evidence}
