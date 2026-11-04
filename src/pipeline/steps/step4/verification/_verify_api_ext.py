# -*- coding: utf-8 -*-
"""API 扩展断言 — api_call_count_check / request_body_check。

扩展 _verify_api 的能力：断言 API 调用次数和请求体内容。
用于"应调用3次接口""请求参数应包含 name=张三"等场景。
"""

import json
import logging
from typing import Any

from ._verify_db_ext import _compare_values

_log = logging.getLogger(__name__)


async def verify_api_ext(
    api_tracker, check: dict[str, Any], start_time: float = 0
) -> dict[str, Any]:
    """API 扩展断言分派入口。

    check 格式：
      api_call_count_check:
        {"type": "api_call_count_check",
         "url_pattern": "/api/users",
         "expected_count": 3,
         "operator": "eq|gte|lte|gt|lt"}
      request_body_check:
        {"type": "request_body_check",
         "url_pattern": "/api/users",
         "expected_field": "name",
         "expected_value": "张三"}
    """
    check_type = check.get("type", "")

    if not api_tracker:
        # ★ 无 tracker 视为执行器异常（阻断），不再 silently pass
        return {
            "passed": False,
            "error": "no_api_tracker",
            "evidence": "[执行器异常] 无 api_tracker，API断言无法执行",
        }

    try:
        if check_type == "api_call_count_check":
            return await _check_call_count(api_tracker, check, start_time)
        elif check_type == "request_body_check":
            return await _check_request_body(api_tracker, check, start_time)
        elif check_type == "api_response_body_check":
            return await _check_response_body(api_tracker, check, start_time)
        else:
            # ★ 未知类型视为执行器异常（阻断）
            return {
                "passed": False,
                "error": "unknown_check_type",
                "evidence": "[执行器异常] 未知API断言类型: %s" % check_type,
            }
    except Exception as e:
        _log.error("[ApiExtCheck] 异常: %s", str(e)[:120], exc_info=True)
        # ★ 异常视为执行器故障，断言失败（避免假通过）
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] API断言崩溃: %s" % str(e)[:80],
        }


async def _check_call_count(api_tracker, check, start_time) -> dict[str, Any]:
    """检查 API 调用次数。"""
    from ._url_match import _match_url_pattern

    url_pattern = check.get("url_pattern", "")
    expected_count = check.get("expected_count", 0)
    operator = check.get("operator", "eq")

    if not url_pattern:
        return {"passed": False, "evidence": "API[count] 缺少 url_pattern"}

    calls = api_tracker.get_calls_since(start_time)
    matched_calls = [
        c for c in calls if _match_url_pattern(url_pattern, c.get("url", ""))
    ]
    actual_count = len(matched_calls)

    passed = _compare_values(actual_count, expected_count, operator)

    return {
        "passed": passed,
        "evidence": "API[count] %s → %d 次 (期望 %s %d)"
        % (
            url_pattern,
            actual_count,
            operator,
            expected_count,
        ),
    }


async def _check_request_body(api_tracker, check, start_time) -> dict[str, Any]:
    """检查请求体内容。"""
    from ._url_match import _match_url_pattern

    url_pattern = check.get("url_pattern", "")
    expected_field = check.get("expected_field", "")
    expected_value = check.get("expected_value", "")

    if not url_pattern or not expected_field:
        return {
            "passed": False,
            "evidence": "API[body] 缺少 url_pattern 或 expected_field",
        }

    calls = api_tracker.get_calls_since(start_time)
    matched_calls = [
        c for c in calls if _match_url_pattern(url_pattern, c.get("url", ""))
    ]

    if not matched_calls:
        return {"passed": False, "evidence": "API[body] 未匹配到 URL: %s" % url_pattern}

    # 检查最后一次匹配的请求
    call = matched_calls[-1]
    req_body = call.get("req_body", "")
    if not req_body:
        return {"passed": False, "evidence": "API[body] 请求体为空: %s" % url_pattern}

    # 尝试解析 JSON
    try:
        if isinstance(req_body, str):
            body_data = json.loads(req_body)
        else:
            body_data = req_body
    except (json.JSONDecodeError, ValueError):
        # 非 JSON，做字符串匹配
        actual_str = str(req_body)
        passed = str(expected_value) in actual_str
        return {
            "passed": passed,
            "evidence": "API[body] %s 字段 '%s' 字符串匹配"
            % (url_pattern, expected_field),
        }

    # JSON 路径提取
    actual_value = _extract_nested(body_data, expected_field)
    passed = actual_value == expected_value

    return {
        "passed": passed,
        "evidence": "API[body] %s.%s = '%s' (期望 '%s')"
        % (
            url_pattern,
            expected_field,
            actual_value,
            expected_value,
        ),
    }


async def _check_response_body(api_tracker, check, start_time) -> dict[str, Any]:
    """检查响应体字段值，支持 operator（eq/gte/lte/gt/lt/in/range/neq/contains）和 tolerance。

    check 格式：
      {"type": "api_response_body_check",
       "url_pattern": "/api/users",
       "expected_field": "data.age",          # 支持 JSON 点号路径
       "expected_value": 18,
       "operator": "eq",                      # eq/gte/lte/gt/lt/in/range/neq/contains
       "tolerance": 0.02}                      # eq 时数值精度容差
    """
    from ._url_match import _match_url_pattern

    url_pattern = check.get("url_pattern", "")
    expected_field = check.get("expected_field", "")
    expected_value = check.get("expected_value", "")
    operator = (check.get("operator") or "eq").strip().lower()
    tolerance = float(check.get("tolerance") or 0)

    if not url_pattern or not expected_field:
        return {
            "passed": False,
            "evidence": "API[resp] 缺少 url_pattern 或 expected_field",
        }

    calls = api_tracker.get_calls_since(start_time)
    matched_calls = [
        c for c in calls if _match_url_pattern(url_pattern, c.get("url", ""))
    ]

    if not matched_calls:
        return {"passed": False, "evidence": "API[resp] 未匹配到 URL: %s" % url_pattern}

    call = matched_calls[-1]
    resp_body = call.get("response_body") or call.get("resp_body") or ""
    if not resp_body:
        return {"passed": False, "evidence": "API[resp] 响应体为空: %s" % url_pattern}

    # JSON 解析
    try:
        body_data = json.loads(resp_body) if isinstance(resp_body, str) else resp_body
    except (json.JSONDecodeError, ValueError):
        # 非 JSON，只能做 contains 匹配
        actual_str = str(resp_body)
        if operator == "contains":
            passed = str(expected_value) in actual_str
        elif operator == "eq":
            passed = actual_str == str(expected_value)
        else:
            return {
                "passed": False,
                "evidence": "API[resp] 非 JSON 响应，仅支持 eq/contains，当前 operator=%s"
                % operator,
            }
        return {
            "passed": passed,
            "evidence": "API[resp] %s 字符串匹配 (operator=%s)"
            % (url_pattern, operator),
        }

    actual_value = _extract_nested(body_data, expected_field)

    # 类型自适应：尝试数值比较
    try:
        actual_num = float(actual_value)
        expected_num = float(expected_value)
        is_numeric = True
    except (TypeError, ValueError):
        is_numeric = False

    if operator == "eq":
        if is_numeric and tolerance > 0:
            passed = abs(actual_num - expected_num) <= tolerance
            evidence_detail = "数值精度 |%g - %g| ≤ %g" % (
                actual_num,
                expected_num,
                tolerance,
            )
        elif is_numeric:
            passed = actual_num == expected_num
            evidence_detail = "数值严格相等"
        else:
            passed = actual_value == expected_value
            evidence_detail = "严格相等"
    elif operator == "neq":
        passed = actual_value != expected_value
        evidence_detail = "不等"
    elif operator in ("gte", "lte", "gt", "lt"):
        if not is_numeric:
            return {
                "passed": False,
                "evidence": "API[resp] operator=%s 需要数值类型，实际=%s"
                % (operator, actual_value),
            }
        passed = _compare_values(actual_num, expected_num, operator)
        evidence_detail = "数值比较 %g %s %g" % (actual_num, operator, expected_num)
    elif operator == "in":
        if isinstance(expected_value, str):
            expected_list = [x.strip() for x in expected_value.split(",")]
        elif isinstance(expected_value, list):
            expected_list = [str(x) for x in expected_value]
        else:
            expected_list = [str(expected_value)]
        passed = str(actual_value) in expected_list
        evidence_detail = "集合包含 actual=%s in %s" % (actual_value, expected_list)
    elif operator == "range":
        # ★ ADI tail  [已复核-DeepSeek-V3 ]: 同 DB 执行器修复——
        #   range 在 "min,max" 字符串下原不可达（顶部 float(expected_value) 抛错），
        #   改为在 range 分支内独立解析 min/max 与实际值。
        if (
            not isinstance(expected_value, str)
            or "," not in expected_value
        ):
            return {
                "passed": False,
                "evidence": "API[resp] range 需要 expected_value='min,max' 且为数值",
            }
        parts = expected_value.split(",")
        try:
            min_v, max_v = float(parts[0]), float(parts[1])
            actual_num_r = float(actual_value)
            passed = min_v <= actual_num_r <= max_v
            evidence_detail = "范围比较 %g ≤ %g ≤ %g" % (min_v, actual_num_r, max_v)
        except (ValueError, IndexError):
            return {
                "passed": False,
                "evidence": "API[resp] range expected_value 格式错误: %s"
                % expected_value,
            }
    elif operator == "contains":
        # 字符串包含
        passed = str(expected_value) in str(actual_value)
        evidence_detail = "字符串包含 '%s'" % expected_value
    else:
        return {"passed": False, "evidence": "API[resp] 未知 operator: %s" % operator}

    return {
        "passed": passed,
        "evidence": "API[resp] %s.%s = '%s' (期望 %s %s) → %s | %s"
        % (
            url_pattern,
            expected_field,
            actual_value,
            operator,
            expected_value,
            "PASS" if passed else "FAIL",
            evidence_detail,
        ),
    }


def _extract_nested(data, path: str):
    """从嵌套 JSON 中提取值，支持点号路径如 'data.name'。"""
    if not data or not path:
        return None
    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current
