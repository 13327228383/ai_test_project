# -*- coding: utf-8 -*-
"""
浏览器 AI 工具调用结果转换 (BrowserAiTools)
============================================

单元 E：纯数据转换 — 将工具调用结果转换为 dispatch 链兼容的 actions 格式。

本模块无 self / engine 引用，仅做数据映射与格式转换。
"""

import logging

from src.pipeline.steps.step4.browser_tools import (
    ToolExecutionResult,
    TOOL_TO_ACTION_MAP,
)

_log = logging.getLogger(__name__)


def tool_results_to_actions(
    tool_calls: list[dict], results: list[ToolExecutionResult]
) -> list[dict]:
    """将工具调用结果转换为现有 dispatch 链兼容的 actions 格式。"""
    actions = []
    # ★ Python 3.9 兼容：不使用 strict 参数
    for tc, result in zip(tool_calls, results):
        name = tc.get("name", "")
        args = tc.get("arguments", {})

        if name == "finish":
            # finish → 转换为 verify action
            success = args.get("success", result.ok)
            reason = args.get("result_summary", result.content[:200])
            # evaluation 优先从 args 读取，fallback 到 result.extra（工具执行时注入）
            evaluation = args.get("evaluation", "") or (result.extra or {}).get(
                "_evaluation", ""
            )
            action = {
                "action": "verify",
                "result": bool(success),
                "reason": reason,
                "_tool_mode": True,
            }
            if evaluation:
                action["_ai_evaluation"] = evaluation
            actions.append(action)
            continue

        action_type = TOOL_TO_ACTION_MAP.get(name)
        if not action_type:
            _log.debug("    [ToolMode] 未知工具 %s 跳过", name)
            continue

        action = {"action": action_type, "_tool_mode": True}

        # 参数映射
        if "target_hint" in args:
            action["target_hint"] = args["target_hint"]
        if "text" in args:
            action["text"] = args["text"]
        if "key" in args:
            action["key"] = args["key"]
        if "direction" in args:
            action["direction"] = args["direction"]
        if "amount" in args:
            action["amount"] = args["amount"]
        if "seconds" in args:
            action["amount"] = args["seconds"]
        if "url" in args:
            action["url"] = args["url"]
        if "file_path" in args:
            action["file_path"] = args["file_path"]
        if "value" in args:
            action["value"] = args["value"]
        if "expected_text" in args:
            action["reason"] = "验证文本: " + args["expected_text"]
            action["result"] = result.ok

        # 执行失败时附加警告
        if not result.ok:
            action["_tool_error"] = result.content[:200]

        actions.append(action)

    return actions
