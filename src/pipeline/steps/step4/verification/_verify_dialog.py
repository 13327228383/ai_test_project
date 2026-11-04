# -*- coding: utf-8 -*-
"""弹窗断言 — dialog_check。

验证原生 JS 弹窗（alert/confirm/prompt）的类型与文本。
依赖 lifecycle.py 中 _on_dialog 缓存到 engine._captured_dialogs。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def verify_dialog(engine, check: dict[str, Any]) -> dict[str, Any]:
    """验证原生 JS 弹窗。

    check 格式：
        {"type": "dialog_check",
         "expected_type": "alert",        # 可选：alert/confirm/prompt
         "expected_text": "保存成功"}      # 可选：弹窗文本（支持包含匹配）
    """
    expected_type = (check.get("expected_type") or "").strip().lower()
    expected_text = check.get("expected_text") or ""

    dialogs = getattr(engine, "_captured_dialogs", None) or []
    if not dialogs:
        return {
            "passed": False,
            "evidence": "Dialog[check] 未捕获到任何弹窗",
        }

    # 取最后一次捕获的弹窗
    last_dialog = dialogs[-1]
    actual_type = last_dialog.get("type", "")
    actual_text = last_dialog.get("message", "")

    passed = True
    reasons = []

    if expected_type and actual_type != expected_type:
        passed = False
        reasons.append("类型不匹配 (实际=%s, 期望=%s)" % (actual_type, expected_type))

    if expected_text and str(expected_text) not in actual_text:
        passed = False
        reasons.append("文本不包含 '%s' (实际=%s)" % (expected_text, actual_text[:80]))

    if not reasons:
        reasons.append("类型=%s, 文本='%s'" % (actual_type, actual_text[:80]))

    return {
        "passed": passed,
        "evidence": "Dialog[check] 共捕获 %d 个弹窗，最后一个: %s → %s"
        % (
            len(dialogs),
            reasons[0],
            "PASS" if passed else "FAIL",
        ),
    }
