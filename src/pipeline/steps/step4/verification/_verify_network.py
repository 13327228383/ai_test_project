# -*- coding: utf-8 -*-
"""网络请求拦截断言 — network_check。

场景：禁用第三方统计/敏感数据外发；关键 API 应被调用 N 次。
缺失后果：合规漏洞漏测（如未授权的第三方请求）；关键流程未触发漏测。

实现：
  1. 从 api_tracker._recent_requests 过滤匹配 url_pattern 的请求。
  2. expected=true → 应有请求；expected=false → 应无请求。
  3. 可选 expected_count + operator 精确校验调用次数。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_network(
    api_tracker, check: dict[str, Any], start_time: float = 0
) -> dict[str, Any]:
    """网络请求拦截断言。

    check 格式：
      {"type": "network_check",
       "url_pattern": "google-analytics.com",        # URL 子串匹配
       "expected": false,                              # false=应无请求（拦截）/ true=应有请求
       "expected_count": 0,                            # 可选，期望次数
       "operator": "eq"}                               # 可选，eq/gte/lte
    """
    try:
        url_pattern = check.get("url_pattern", "")
        expected = check.get("expected", True)
        if isinstance(expected, str):
            expected = expected.lower() not in ("false", "0", "no", "")
        expected_count = check.get("expected_count")
        operator = (check.get("operator") or "eq").strip().lower()

        if not url_pattern:
            return {"passed": False, "evidence": "[NetworkCheck] 缺少 url_pattern 参数"}

        # 从 api_tracker 取请求记录
        records = []
        if api_tracker:
            records = getattr(api_tracker, "_recent_requests", []) or []

        # 过滤匹配 url_pattern 的请求
        matched = []
        for r in records:
            if not isinstance(r, dict):
                continue
            url = str(r.get("url", ""))
            if url_pattern in url:
                matched.append(r)
        actual_count = len(matched)

        # 校验
        if expected_count is not None:
            # 精确次数校验
            expected_count = int(expected_count)
            if operator == "eq":
                passed = actual_count == expected_count
            elif operator == "gte":
                passed = actual_count >= expected_count
            elif operator == "lte":
                passed = actual_count <= expected_count
            else:
                return {
                    "passed": False,
                    "evidence": "[NetworkCheck] 未知 operator: %s" % operator,
                }
            detail = "count=%d (期望 %s %d)" % (actual_count, operator, expected_count)
        else:
            # 存在性校验
            if expected:
                passed = actual_count > 0
                detail = "count=%d (期望 > 0)" % actual_count
            else:
                passed = actual_count == 0
                detail = "count=%d (期望 = 0，拦截)" % actual_count

        return {
            "passed": passed,
            "evidence": "[NetworkCheck] url~'%s' → %s | %s → %s"
            % (
                url_pattern,
                detail,
                "; ".join([str(m.get("url", ""))[:40] for m in matched[:3]]),
                "PASS" if passed else "FAIL",
            ),
        }
    except Exception as e:
        _log.error("[NetworkCheck] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] network_check 崩溃: %s" % str(e)[:80],
        }
