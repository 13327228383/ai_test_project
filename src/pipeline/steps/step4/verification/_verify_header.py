# -*- coding: utf-8 -*-
"""API 响应头断言 — api_header_check。

场景：Content-Type 应为 application/json；Authorization 应存在；Cache-Control 应为 no-cache。
缺失后果：安全合规规则漏测（如响应未带 X-Frame-Options 导致点击劫持）。

实现：
  1. 从 api_tracker._recent_requests 取匹配 url_pattern 的最近请求。
  2. 取响应头 response_headers。
  3. operator 支持 exists/not_exists/eq/contains。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_api_header(
    api_tracker, check: dict[str, Any], start_time: float = 0
) -> dict[str, Any]:
    """API 响应头断言。

    check 格式：
      {"type": "api_header_check",
       "url_pattern": "/api/login",                  # URL 子串匹配
       "header_name": "Content-Type",                # 响应头名称
       "expected": "application/json",                # 期望值（exists/not_exists 可不填）
       "operator": "eq"}                              # exists/not_exists/eq/contains
    """
    try:
        url_pattern = check.get("url_pattern", "")
        header_name = check.get("header_name", "")
        expected = check.get("expected", "")
        operator = (check.get("operator") or "exists").strip().lower()

        if not url_pattern:
            return {"passed": False, "evidence": "[HeaderCheck] 缺少 url_pattern 参数"}
        if not header_name:
            return {"passed": False, "evidence": "[HeaderCheck] 缺少 header_name 参数"}
        if operator not in ("exists", "not_exists", "eq", "contains"):
            return {
                "passed": False,
                "evidence": "[HeaderCheck] 未知 operator: %s" % operator,
            }

        # 取请求记录
        records = []
        if api_tracker:
            records = getattr(api_tracker, "_recent_requests", []) or []

        matched_req = None
        for r in reversed(records):
            if not isinstance(r, dict):
                continue
            url = str(r.get("url", ""))
            if url_pattern in url:
                matched_req = r
                break

        if not matched_req:
            return {
                "passed": False,
                "evidence": "[HeaderCheck] 无匹配请求: %s" % url_pattern,
            }

        # 取响应头
        headers = matched_req.get("response_headers", {}) or {}
        actual_value = headers.get(header_name) or headers.get(header_name.lower())
        exists = actual_value is not None

        # 校验
        if operator == "exists":
            passed = exists
        elif operator == "not_exists":
            passed = not exists
        elif operator == "eq":
            passed = exists and str(actual_value).lower() == str(expected).lower()
        else:  # contains
            passed = exists and str(expected).lower() in str(actual_value).lower()

        return {
            "passed": passed,
            "evidence": "[HeaderCheck] %s['%s'] = '%s' exists=%s operator=%s expected='%s' → %s"
            % (
                url_pattern,
                header_name,
                str(actual_value)[:40] if exists else "NULL",
                exists,
                operator,
                str(expected)[:40],
                "PASS" if passed else "FAIL",
            ),
        }
    except Exception as e:
        _log.error("[HeaderCheck] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] api_header_check 崩溃: %s" % str(e)[:80],
        }
