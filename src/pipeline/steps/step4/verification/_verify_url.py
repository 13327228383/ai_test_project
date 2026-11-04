# -*- coding: utf-8 -*-
"""URL 断言 — url_check。

验证当前页面 URL 是否包含/等于预期值，用于路由跳转校验。
支持 operator: contains（默认）/ equals / startswith / endswith / regex。
"""

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)


def verify_url(page, check: dict[str, Any]) -> dict[str, Any]:
    """验证当前页面 URL。

    check 格式：
        {"type": "url_check",
         "expected": "/dashboard",        # 预期 URL 或片段
         "operator": "contains"}          # contains|equals|startswith|endswith|regex
    """
    if not page:
        return {"passed": False, "evidence": "URL[check] page 为空"}

    expected = str(check.get("expected", "") or "")
    operator = (check.get("operator") or "contains").strip().lower()

    if not expected:
        return {"passed": False, "evidence": "URL[check] 缺少 expected 参数"}

    try:
        current_url = page.url or ""
    except Exception as e:
        return {
            "passed": False,
            "evidence": "URL[check] 读取 page.url 失败: %s" % str(e)[:80],
        }

    if operator == "contains":
        passed = expected in current_url
    elif operator == "equals":
        passed = current_url == expected or current_url.rstrip("/") == expected.rstrip(
            "/"
        )
    elif operator == "startswith":
        passed = current_url.startswith(expected)
    elif operator == "endswith":
        passed = current_url.endswith(expected)
    elif operator == "regex":
        try:
            passed = re.search(expected, current_url) is not None
        except re.error as e:
            return {
                "passed": False,
                "evidence": "URL[check] 非法正则 %s: %s" % (expected, str(e)[:80]),
            }
    else:
        return {"passed": False, "evidence": "URL[check] 未知 operator: %s" % operator}

    return {
        "passed": passed,
        "evidence": "URL[%s] current=%s expected=%s → %s"
        % (
            operator,
            current_url,
            expected,
            "PASS" if passed else "FAIL",
        ),
    }
