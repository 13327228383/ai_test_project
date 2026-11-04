# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Schema 校验器
=============
- 校验 tool_call 格式、必填字段、类型、enum 值
"""

import json
from typing import Any

from src.pipeline.steps.step4.browser_tools.query import get_tool_schema, tool_name_list
import logging

_log = logging.getLogger(__name__)


class SchemaValidationError:
    """校验结果错误信息。"""

    def __init__(self, tool_name: str, message: str, field: str | None = None):
        self.tool_name = tool_name
        self.message = message
        self.field = field

    def __str__(self) -> str:
        if self.field:
            return "[{0}] field '{1}': {2}".format(
                self.tool_name, self.field, self.message
            )
        return "[{0}]: {1}".format(self.tool_name, self.message)


def validate_tool_call(
    tool_call: dict[str, Any],
) -> tuple[bool, SchemaValidationError | None, str, dict[str, Any]]:
    """校验一个 tool_call 是否合法。返回 (ok, err, tool_name, normalized_args)。"""
    tool_name = ""
    raw_args: dict[str, Any] = {}

    # 支持两种输入格式：
    # 1) 原生 OpenAI 格式 {"id": ..., "type": "function", "function": {"name": ..., "arguments": ...}}
    # 2) 手写格式 {"name": ..., "arguments": ...}
    if isinstance(tool_call, dict) and "function" in tool_call:
        fn = tool_call.get("function", {})
        tool_name = fn.get("name", "")
        raw_args = fn.get("arguments", {})
    elif isinstance(tool_call, dict):
        tool_name = tool_call.get("name", "")
        raw_args = tool_call.get("arguments", {})
    else:
        return False, SchemaValidationError("", "tool_call 格式不对"), "", {}

    # tool_name 合法性
    if not tool_name or not isinstance(tool_name, str) or not tool_name.strip():
        return False, SchemaValidationError("", "工具名缺失或非法", "name"), "", {}

    # 判断工具是否存在
    legal_names = set(tool_name_list())
    if tool_name not in legal_names:
        return (
            False,
            SchemaValidationError(
                tool_name,
                "不在合法工具列表中。合法工具: " + str(sorted(legal_names)),
                "name",
            ),
            tool_name,
            {},
        )

    # 取 schema
    schema = get_tool_schema(tool_name)
    if not schema:
        return (
            False,
            SchemaValidationError(tool_name, "找不到 Schema", "name"),
            tool_name,
            {},
        )

    params = schema.get("function", {}).get("parameters", {})
    properties = (
        params.get("properties", {})
        if isinstance(params.get("properties"), dict)
        else {}
    )
    required_fields = (
        params.get("required", []) if isinstance(params.get("required"), list) else []
    )

    # arguments 处理：支持 dict 或 JSON string
    arguments: dict[str, Any] = {}
    if isinstance(raw_args, str):
        stripped = raw_args.strip()
        if stripped:
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    arguments = parsed
                else:
                    msg = (
                        "arguments 解析后不是 dict, 实际类型: " + type(parsed).__name__
                    )
                    return (
                        False,
                        SchemaValidationError(tool_name, msg, "arguments"),
                        tool_name,
                        {},
                    )
            except Exception as _e:
                _log.debug("[validation] 非关键异常: %s", _e, exc_info=True)
                return (
                    False,
                    SchemaValidationError(
                        tool_name,
                        "arguments 不是合法 JSON: " + stripped[:50],
                        "arguments",
                    ),
                    tool_name,
                    {},
                )
    elif isinstance(raw_args, dict):
        arguments = raw_args
    else:
        return (
            False,
            SchemaValidationError(
                tool_name, "arguments 类型错误: " + type(raw_args).__name__, "arguments"
            ),
            tool_name,
            {},
        )

    # 必填字段校验
    for req_field in required_fields:
        value = arguments.get(req_field)
        if value is None or (isinstance(value, str) and value == ""):
            return (
                False,
                SchemaValidationError(
                    tool_name, "必填字段缺失: " + req_field, req_field
                ),
                tool_name,
                {},
            )

    # 类型 + enum 校验
    for field_name, spec in properties.items():
        spec = spec if isinstance(spec, dict) else {}
        ftype = spec.get("type")
        if not isinstance(ftype, str):
            continue
        value = arguments.get(field_name)
        if value is None or (isinstance(value, str) and value == ""):
            continue  # 可选字段允许为空
        if ftype == "string":
            if not isinstance(value, str):
                return (
                    False,
                    SchemaValidationError(
                        tool_name,
                        "字段 "
                        + field_name
                        + " 类型错误: 期望 string, 实际 "
                        + type(value).__name__,
                        field_name,
                    ),
                    tool_name,
                    {},
                )
            enum_vals = spec.get("enum")
            if enum_vals and isinstance(enum_vals, list):
                if value not in enum_vals:
                    vals_str = ", ".join(str(v) for v in enum_vals)
                    return (
                        False,
                        SchemaValidationError(
                            tool_name,
                            "字段 " + field_name + " 值不在合法范围: " + vals_str,
                            field_name,
                        ),
                        tool_name,
                        {},
                    )
        elif ftype in ("integer", "number"):
            if isinstance(value, bool):
                return (
                    False,
                    SchemaValidationError(
                        tool_name,
                        "字段 " + field_name + " 类型错误: 期望 " + ftype,
                        field_name,
                    ),
                    tool_name,
                    {},
                )
            if not isinstance(value, (int, float)):
                return (
                    False,
                    SchemaValidationError(
                        tool_name,
                        "字段 " + field_name + " 类型错误: 期望 " + ftype,
                        field_name,
                    ),
                    tool_name,
                    {},
                )
        elif ftype == "boolean":
            if not isinstance(value, bool):
                return (
                    False,
                    SchemaValidationError(
                        tool_name,
                        "字段 " + field_name + " 类型错误: 期望 boolean",
                        field_name,
                    ),
                    tool_name,
                    {},
                )
        elif ftype == "array":
            if not isinstance(value, list):
                return (
                    False,
                    SchemaValidationError(
                        tool_name,
                        "字段 " + field_name + " 类型错误: 期望 array",
                        field_name,
                    ),
                    tool_name,
                    {},
                )
        # object 类型跳过深度校验

    return True, None, tool_name, arguments
