# -*- coding: utf-8 -*-
"""API 性能断言 — response_time_check / response_header_check。

从 ApiTracker 的请求记录中提取响应时间和响应头进行校验。

★ T03 fail-open 修复（主理人决策 #3）：
- 异常 → FAIL（原 passed=True 假通过）
- 无请求 / 未匹配 URL → UNKNOWN+skipped（不计 AND 但置信度扣分）
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_performance(api_tracker, check: dict[str, Any]) -> dict[str, Any]:
    """API 性能断言。

    check 格式：
      {"type": "response_time_check",
       "url_pattern": "API URL 匹配模式",
       "max_ms": 最大允许耗时毫秒}

      {"type": "response_header_check",
       "url_pattern": "API URL 匹配模式",
       "header_name": "响应头名称",
       "expected": "期望值"}
    """
    check_type = check.get("type", "")
    if not api_tracker:
        return {
            "passed": True,
            "skipped": True,
            "status": "UNKNOWN",
            "skip_reason": "no_api_tracker",
            "evidence": "无 api_tracker，性能断言跳过",
        }

    try:
        if check_type == "response_time_check":
            return await _check_response_time(api_tracker, check)
        elif check_type == "response_header_check":
            return await _check_response_header(api_tracker, check)
        else:
            # ★ T03: 未知类型 fail-closed（原 passed=True 假通过）
            return {
                "passed": False,
                "status": "FAIL",
                "evidence": "未知性能断言类型(%s)，判定失败" % check_type,
            }
    except Exception as e:
        _log.warning("[PerfCheck] 异常: %s", str(e)[:120])
        # ★ T03: 异常 → FAIL（原 passed=True 假通过）
        return {
            "passed": False,
            "status": "FAIL",
            "evidence": "性能断言异常(fail-closed): %s" % str(e)[:80],
        }


async def _check_response_time(api_tracker, check) -> dict[str, Any]:
    """响应时间校验。"""
    url_pattern = check.get("url_pattern", "")
    max_ms = check.get("max_ms", 5000)

    requests = _get_recent_requests(api_tracker)
    if not requests:
        return {
            "passed": True,
            "skipped": True,
            "status": "UNKNOWN",
            "skip_reason": "no_api_requests",
            "evidence": "性能[response_time_check] 无 API 请求记录",
        }

    matched = _find_by_url(requests, url_pattern)
    if not matched:
        return {
            "passed": True,
            "skipped": True,
            "status": "UNKNOWN",
            "skip_reason": "url_not_matched",
            "evidence": "性能[response_time_check] 未找到匹配 URL: %s" % url_pattern,
        }

    req = matched[-1]
    elapsed = req.get("elapsed_ms", 0)
    passed = elapsed <= max_ms
    result = {
        "passed": passed,
        "evidence": "性能[response_time_check] %s 耗时=%dms (阈值%dms)"
        % (req.get("url", "")[:60], elapsed, max_ms),
    }
    # ★ W3 T05 (2026-08-05): 透传 perf_ms 供性能采集落 oracle_results.detail
    #   （原 ApiTracker 不记 elapsed_ms → 恒 0/UNKNOWN；修复后真实耗时进入结果）。
    result["perf_ms"] = int(elapsed or 0)
    result["perf_url"] = str(req.get("url", ""))[:200]
    return result


async def _check_response_header(api_tracker, check) -> dict[str, Any]:
    """响应头校验。"""
    url_pattern = check.get("url_pattern", "")
    header_name = check.get("header_name", "")
    expected = check.get("expected", "")

    requests = _get_recent_requests(api_tracker)
    if not requests:
        return {
            "passed": True,
            "skipped": True,
            "status": "UNKNOWN",
            "skip_reason": "no_api_requests",
            "evidence": "性能[response_header_check] 无 API 请求记录",
        }

    matched = _find_by_url(requests, url_pattern)
    if not matched:
        return {
            "passed": True,
            "skipped": True,
            "status": "UNKNOWN",
            "skip_reason": "url_not_matched",
            "evidence": "性能[response_header_check] 未找到匹配 URL: %s" % url_pattern,
        }

    req = matched[-1]
    headers = req.get("response_headers", {})
    actual = headers.get(header_name, headers.get(header_name.lower(), ""))
    passed = str(actual) == str(expected)
    return {
        "passed": passed,
        "evidence": "性能[response_header_check] %s=%s (期望%s)"
        % (header_name, actual, expected),
    }


def _get_recent_requests(api_tracker) -> list:
    """从 api_tracker 获取最近的请求列表。"""
    if hasattr(api_tracker, "_recent_requests"):
        return list(api_tracker._recent_requests)
    if hasattr(api_tracker, "requests"):
        return list(api_tracker.requests)
    return []


def _find_by_url(requests: list, url_pattern: str) -> list:
    """按 URL 模式过滤请求（包含匹配）。"""
    if not url_pattern:
        return list(requests)
    return [r for r in requests if url_pattern in r.get("url", "")]
