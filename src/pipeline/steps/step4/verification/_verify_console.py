# -*- coding: utf-8 -*-
"""Console 错误断言 — console_error_check。

验证浏览器 console 无 JS 错误（pageerror / console.error）。
依赖 lifecycle.py 中 _on_console / _on_pageerror 监听缓存到 engine._console_errors。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_no_console_error(engine, check: dict[str, Any]) -> dict[str, Any]:
    """验证浏览器 console 无 JS 错误。

    check 格式：
        {"type": "console_error_check",
         "expected": true}    # true=应无错误，false=应有错误（用于反向断言）
    """
    expected = check.get("expected", True)
    if isinstance(expected, str):
        expected = expected.lower() not in ("false", "0", "no", "")

    errors = getattr(engine, "_console_errors", None) or []
    error_count = len(errors)
    has_errors = error_count > 0

    # expected=True 表示"应无错误"，has_errors=True 表示"有错误" → 不通过
    passed = (not has_errors) if expected else has_errors

    # 拼接错误摘要（最多 3 条，避免 evidence 过长）
    error_summary = ""
    if errors:
        sample = errors[:3]
        parts = []
        for e in sample:
            t = e.get("type", "unknown")
            text = (e.get("text") or "")[:80]
            parts.append("[%s] %s" % (t, text))
        error_summary = "; ".join(parts)
        if error_count > 3:
            error_summary += " (共 %d 条，仅显示前 3 条)" % error_count

    return {
        "passed": passed,
        "evidence": "Console[error] 共 %d 条 JS 错误 (期望%s) %s"
        % (
            error_count,
            "无错误" if expected else "有错误",
            "→ " + error_summary if error_summary else "",
        ),
    }
