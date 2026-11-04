# -*- coding: utf-8 -*-
"""WebSocket 消息断言 — websocket_check。

场景：实时推送消息内容校验；交易系统价格推送；聊天室消息收发。
缺失后果：实时通信场景漏测（无法校验推送内容）。

实现：
  1. 从 engine._websocket_messages 取最近的消息记录。
  2. 按 operator（contains/equals/regex）匹配 expected_message。
  3. 找到匹配即通过，否则失败。

依赖：engine 需在 lifecycle.py 中注册 page.on("websocket") hook，
      将收到的帧存入 engine._websocket_messages。
"""

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)


def verify_websocket(engine, check: dict[str, Any]) -> dict[str, Any]:
    """WebSocket 消息断言。

    check 格式：
      {"type": "websocket_check",
       "expected_message": "price_update",          # 期望消息内容
       "operator": "contains"}                        # contains/equals/regex
    """
    try:
        expected_message = check.get("expected_message", "")
        operator = (check.get("operator") or "contains").strip().lower()

        if not expected_message:
            return {"passed": False, "evidence": "[WsCheck] 缺少 expected_message 参数"}
        if operator not in ("contains", "equals", "regex"):
            return {
                "passed": False,
                "evidence": "[WsCheck] 未知 operator: %s" % operator,
            }

        messages = getattr(engine, "_websocket_messages", None)
        if messages is None:
            return {
                "passed": False,
                "evidence": "[WsCheck] 未启用 WebSocket 监听（engine._websocket_messages 为空）",
            }

        # 编译正则（regex 模式）
        regex = None
        if operator == "regex":
            try:
                regex = re.compile(expected_message)
            except re.error as e:
                return {
                    "passed": False,
                    "evidence": "[WsCheck] 非法正则 '%s': %s"
                    % (expected_message, str(e)[:60]),
                }

        # 查找匹配的消息
        matched = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            data = str(msg.get("data", ""))
            if operator == "contains" and expected_message in data:
                matched.append(msg)
            elif operator == "equals" and data == expected_message:
                matched.append(msg)
            elif operator == "regex" and regex and regex.search(data):
                matched.append(msg)

        passed = len(matched) > 0
        sample = str(matched[0].get("data", ""))[:60] if matched else "无匹配"
        return {
            "passed": passed,
            "evidence": "[WsCheck] 共 %d 条 WS 消息，匹配 %d 条 operator=%s → %s | 样本: %s"
            % (
                len(messages),
                len(matched),
                operator,
                "PASS" if passed else "FAIL",
                sample,
            ),
        }
    except Exception as e:
        _log.error("[WsCheck] 异常: %s", str(e)[:120], exc_info=True)
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] websocket_check 崩溃: %s" % str(e)[:80],
        }
