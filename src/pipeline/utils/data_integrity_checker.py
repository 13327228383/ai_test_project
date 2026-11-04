# -*- coding: utf-8 -*-
from __future__ import annotations

"""
data_integrity_checker.py — 数据完整性校验器
============================================
为每个 Step 消费者提供统一的输入数据结构校验。
校验规则：
1. 必填字段非空（None / 空字符串 / 空列表视为缺失）
2. 枚举值在合法域内
3. 数据类型匹配（list/dict/str/int）

纯函数，无副作用，可独立单元测试。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


def validate_step_input(
    table_name: str,
    records: list[dict[str, Any]],
    required_fields: list[str],
    enum_fields: dict[str, list[str]] | None = None,
    type_fields: dict[str, type] | None = None,
) -> dict[str, Any]:
    """校验 Step 输入数据的结构完整性。

    参数:
        table_name: 数据来源表名（用于日志标识）
        records: 待校验的记录列表（通常从 MySQL 读取）
        required_fields: 必填字段名列表
        enum_fields: 枚举字段及其合法值 {"field_name": ["val1", "val2", ...]}
        type_fields: 类型约束 {"field_name": list/dict/str/int}

    返回:
        {
            "valid": bool,           # 全部记录通过校验
            "total": int,            # 总记录数
            "passed": int,           # 通过校验的记录数
            "failed": int,           # 未通过校验的记录数
            "errors": [              # 错误详情
                {"index": 0, "field": "name", "error": "missing", "value": None},
                ...
            ],
        }
    """
    if not records:
        return {
            "valid": True,
            "total": 0,
            "passed": 0,
            "failed": 0,
            "errors": [],
        }

    enum_fields = enum_fields or {}
    type_fields = type_fields or {}

    total = len(records)
    passed = 0
    errors = []

    for idx, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(
                {
                    "index": idx,
                    "field": "<root>",
                    "error": "not_dict",
                    "value": type(record).__name__,
                }
            )
            continue

        record_ok = True

        # 1. 必填字段校验
        for field in required_fields:
            value = record.get(field)
            if value is None or value == "" or value == []:
                record_ok = False
                errors.append(
                    {
                        "index": idx,
                        "field": field,
                        "error": "missing",
                        "value": repr(value)[:60],
                    }
                )

        # 2. 枚举值校验
        for field, valid_values in enum_fields.items():
            value = record.get(field)
            if value is not None and value != "" and value not in valid_values:
                record_ok = False
                errors.append(
                    {
                        "index": idx,
                        "field": field,
                        "error": "invalid_enum",
                        "value": repr(value)[:60],
                        "valid_values": valid_values,
                    }
                )

        # 3. 类型校验
        for field, expected_type in type_fields.items():
            value = record.get(field)
            if (
                value is not None
                and value != ""
                and not isinstance(value, expected_type)
            ):
                record_ok = False
                errors.append(
                    {
                        "index": idx,
                        "field": field,
                        "error": "wrong_type",
                        "value": repr(value)[:60],
                        "expected_type": expected_type.__name__,
                    }
                )

        if record_ok:
            passed += 1

    valid = len(errors) == 0
    if not valid:
        _log.warning(
            "[IntegrityCheck] %s: %d/%d records failed (%d errors)",
            table_name,
            total - passed,
            total,
            len(errors),
        )

    return {
        "valid": valid,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "errors": errors[:50],  # 最多返回50条错误
    }
