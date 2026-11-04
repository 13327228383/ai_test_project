# -*- coding: utf-8 -*-
from __future__ import annotations

"""
测试用例可执行性检查模块
======================
检查 Step4 执行前测试用例是否具备最低可执行信息。
"""

from typing import Any

from src.pipeline.utils.consistency_helpers import (
    Issue,
    _parse_steps,
    _make_issue,
    _safe_get_case_id,
    _count_severity,
)


def check_intent_executability(excel_handler: Any | None = None) -> dict[str, Any]:
    """检查 Step4 执行前测试用例是否具备最低可执行信息。"""
    if excel_handler is None:
        from src.core.excel_handler import excel_handler as excel_handler

    issues: list[Issue] = []
    intents = excel_handler.read_test_cases()
    missing_steps = []
    missing_expected = []

    for intent in intents:
        cid = _safe_get_case_id(intent)
        if not cid:
            continue
        # v9: title 即为意图核心
        has_intent = bool(str(intent.get("title") or "").strip())
        if not has_intent and not _parse_steps(intent.get("case_actions_json")):
            missing_steps.append(cid)
        if not str(intent.get("expected_outcome", "")).strip():
            missing_expected.append(cid)

    if missing_steps:
        issues.append(
            _make_issue(
                "INTENT_WITHOUT_STEPS",
                "error",
                "测试用例缺少可执行步骤",
                missing_steps,
            )
        )
    if missing_expected:
        issues.append(
            _make_issue(
                "INTENT_WITHOUT_EXPECTED_RESULT",
                "warning",
                "测试用例缺少预期结果，执行可解释性较弱",
                missing_expected,
            )
        )

    return {
        "ok": not any(i.get("severity") == "error" for i in issues),
        "issues": issues,
        "stats": {
            "intent_count": len(intents),
            "error_count": _count_severity(issues, "error"),
            "warning_count": _count_severity(issues, "warning"),
        },
    }
