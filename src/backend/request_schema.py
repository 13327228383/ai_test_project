# -*- coding: utf-8 -*-
"""
★ KEEP-30: API 请求 schema 校验基础层。

轻量实现，不引入额外依赖；用于统一校验 JSON body 的类型、必填项、
枚举值、字符串长度和正则模式。失败时返回标准 400 JSON 响应。
"""

from __future__ import annotations

import re
from typing import Any
from flask import jsonify, request
from src.core.exceptions import ValidationError


def validate_json_schema(
    schema: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], Any]:
    """按 schema 校验 request JSON。

    schema 示例：
        {
            "name": {"type": str, "required": True, "max_len": 64},
            "status": {"type": str, "enum": ["draft", "active"]},
        }

    返回：(data, error_response)。error_response 非 None 时应直接 return。
    """
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        err = ValidationError("请求体必须是 JSON 对象")
        return {}, (jsonify(err.to_dict()), err.status_code)

    errors = []
    cleaned: dict[str, Any] = {}

    for field, rule in schema.items():
        required = bool(rule.get("required", False))
        expected_type = rule.get("type")
        value = data.get(field)

        if value is None:
            if required:
                errors.append(f"{field} 为必填字段")
            continue

        if expected_type is not None and not isinstance(value, expected_type):
            errors.append(f"{field} 类型错误，期望 {expected_type.__name__}")
            continue

        if isinstance(value, str):
            value = value.strip()
            min_len = rule.get("min_len")
            max_len = rule.get("max_len")
            if min_len is not None and len(value) < int(min_len):
                errors.append(f"{field} 长度不能小于 {min_len}")
            if max_len is not None and len(value) > int(max_len):
                errors.append(f"{field} 长度不能超过 {max_len}")
            pattern = rule.get("pattern")
            if pattern is not None and not re.match(pattern, value):
                errors.append(f"{field} 格式不正确")

        enum = rule.get("enum")
        if enum is not None and value not in enum:
            errors.append(f"{field} 只能是: {', '.join(map(str, enum))}")

        cleaned[field] = value

    if errors:
        err = ValidationError("请求参数校验失败", details=errors)
        return {}, (jsonify(err.to_dict()), err.status_code)
    return cleaned, None
