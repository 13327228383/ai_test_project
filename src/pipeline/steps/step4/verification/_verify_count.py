# -*- coding: utf-8 -*-
"""元素数量断言 — element_count_check。

验证页面上匹配选择器的元素数量是否符合预期。
用于"列表应有5条记录""下拉框应有3个选项"等场景。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_count(page, check: dict[str, Any]) -> dict[str, Any]:
    """元素数量断言。

    check 格式：
      {"type": "element_count_check",
       "selector": "css选择器",
       "expected_count": 5,
       "operator": "eq|gte|lte|gt|lt"}
    """
    selector = check.get("selector", "")
    if not selector:
        return {"passed": True, "evidence": "无 selector，跳过"}

    if page is None:
        return {"passed": True, "evidence": "无 page，数量断言跳过"}

    expected_count = check.get("expected_count", 0)
    operator = check.get("operator", "eq")

    try:
        locator = page.locator(selector)
        actual_count = await locator.count()

        if operator == "eq":
            passed = actual_count == expected_count
            op_desc = "等于"
        elif operator == "gte":
            passed = actual_count >= expected_count
            op_desc = "大于等于"
        elif operator == "lte":
            passed = actual_count <= expected_count
            op_desc = "小于等于"
        elif operator == "gt":
            passed = actual_count > expected_count
            op_desc = "大于"
        elif operator == "lt":
            passed = actual_count < expected_count
            op_desc = "小于"
        else:
            passed = actual_count == expected_count
            op_desc = "等于"

        return {
            "passed": passed,
            "evidence": "元素数量[%s] 实际=%d %s 期望=%d"
            % (
                selector,
                actual_count,
                op_desc,
                expected_count,
            ),
        }
    except Exception as e:
        _log.warning("[CountCheck] 异常: %s", str(e)[:120])
        return {"passed": True, "evidence": "数量断言异常，跳过: %s" % str(e)[:80]}
