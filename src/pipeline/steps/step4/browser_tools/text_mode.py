# -*- coding: utf-8 -*-
from __future__ import annotations

"""
文本模式（降级路径）
====================
- 模型不支持 function_calling 时使用的文本描述与解析
"""

import json
from typing import Any

from src.pipeline.steps.step4.browser_tools.schemas import ALL_TOOLS
import logging

_log = logging.getLogger(__name__)


def build_text_mode_tool_description() -> str:
    """生成工具说明文本（用于喂给不支持原生 function_calling 的模型）。"""
    lines = []
    lines.append(
        "以下是你可以调用的工具（以 JSON 数组格式输出 tool_calls 来调用，不得写散文）："
    )
    lines.append("输出格式必须是:")
    lines.append('[{"name": "tool_name", "arguments": {...}}, ...]')
    lines.append("")
    lines.append("工具列表:")
    for t in ALL_TOOLS:
        fn = t.get("function", {})
        name = fn.get("name", "")
        desc = fn.get("description", "")
        lines.append("- " + name + ": " + desc)
        params = fn.get("parameters", {})
        if isinstance(params, dict):
            properties = params.get("properties", {})
            required = params.get("required", [])
            if isinstance(properties, dict):
                for key, spec in properties.items():
                    if not isinstance(spec, dict):
                        continue
                    tag = "(必填)" if key in required else "(可选)"
                    typ = spec.get("type", "string")
                    d = spec.get("description", "")
                    enum_vals = spec.get("enum")
                    if enum_vals and isinstance(enum_vals, list):
                        vs = ", ".join(str(v) for v in enum_vals)
                        lines.append(
                            "    "
                            + key
                            + " {"
                            + typ
                            + "} "
                            + tag
                            + ": "
                            + d
                            + " 合法值: "
                            + vs
                        )
                    else:
                        lines.append("    " + key + " {" + typ + "} " + tag + ": " + d)
        lines.append("")
    lines.append("必须以 JSON 数组输出 tool_calls，不得写散文描述。")
    return "\n".join(lines)


def parse_text_mode_tool_calls(
    raw_text: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """从 AI 纯文本响应解析 tool_calls 列表。返回 (tool_calls_list, error_or_None)。"""
    if not raw_text or not raw_text.strip():
        return [], "AI 响应为空"

    text = raw_text.strip()

    # 1) 直接 JSON 解析
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [_normalize_single_tool_call(x) for x in parsed], None
        if isinstance(parsed, dict):
            return [_normalize_single_tool_call(parsed)], None
    except Exception as _e:
        _log.debug("[text_mode] parse_text_mode_tool_calls 异常: %s", _e, exc_info=True)

    # 2) markdown 代码块 ```json ... ```
    if "```" in text:
        idx1 = text.find("```")
        idx2 = text.find("```", idx1 + 3)
        if idx2 > idx1:
            candidate = text[idx1 + 3 : idx2].strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    return [_normalize_single_tool_call(x) for x in parsed], None
                if isinstance(parsed, dict):
                    return [_normalize_single_tool_call(parsed)], None
            except Exception as _e:
                _log.debug("[text_mode] 非关键异常: %s", _e, exc_info=True)

    # 3) [ ... ] 范围提取
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, list):
                return [_normalize_single_tool_call(x) for x in parsed], None
            if isinstance(parsed, dict):
                return [_normalize_single_tool_call(parsed)], None
        except Exception as _e:
            _log.debug("[text_mode] 非关键异常: %s", _e, exc_info=True)

    # 4) { ... } 范围提取
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, list):
                return [_normalize_single_tool_call(x) for x in parsed], None
            if isinstance(parsed, dict):
                return [_normalize_single_tool_call(parsed)], None
        except Exception as _e:
            _log.debug("[text_mode] 非关键异常: %s", _e, exc_info=True)

    return [], "无法从 AI 响应解析出 JSON tool_calls"


def _normalize_single_tool_call(item) -> dict[str, Any]:
    """把单条 tool_call 归一化成标准格式。"""
    if not isinstance(item, dict):
        return {"name": str(item), "arguments": {}}
    # 已是标准格式 {name, arguments}
    if "name" in item and "arguments" in item:
        return item
    # OpenAI 原生格式 {id, type, function: {name, arguments}}
    if "function" in item:
        fn = item.get("function", {})
        if isinstance(fn, dict):
            result = {"name": fn.get("name", ""), "arguments": fn.get("arguments", {})}
            if "id" in item:
                result["id"] = item["id"]
            return result
    # 最小兼容 {"action": "click", ...}
    if "action" in item:
        act = item["action"]
        args = {k: v for k, v in item.items() if k != "action"}
        return {"name": act, "arguments": args}
    return {"name": item.get("name", ""), "arguments": {}}
