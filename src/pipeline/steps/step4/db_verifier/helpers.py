# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""DB 校验通用工具函数 — 从 db_verifier.py 拆出，零逻辑变更。"""

from decimal import Decimal, InvalidOperation
from typing import Any
import logging

_log = logging.getLogger(__name__)


def _compare_with_precision(
    actual: Any, expected: Any, precision: float | None = None
) -> bool:
    """
    带精度的值比较。

    支持类型：
    - 整数：直接比较
    - 浮点数/Decimal：使用精度容差
    - 字符串：去除空白后比较
    - 日期时间：字符串标准化后比较（忽略毫秒差异）
    """
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False

    # 尝试数值比较
    try:
        actual_dec = Decimal(str(actual))
        expected_dec = Decimal(str(expected))
        if precision is not None:
            return abs(actual_dec - expected_dec) <= Decimal(str(precision))
        return actual_dec == expected_dec
    except (InvalidOperation, ValueError):
        _log.debug("  [DB-Verifier] 精度比较无法解析为数值，回退到字符串比较")

    # 日期时间标准化比较（忽略毫秒）
    actual_str = str(actual).strip()
    expected_str = str(expected).strip()

    # 尝试解析为日期时间，去掉毫秒部分
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            from datetime import datetime

            a_dt = datetime.strptime(actual_str[:19], fmt)
            e_dt = datetime.strptime(expected_str[:19], fmt)
            return a_dt == e_dt
        except (ValueError, TypeError):
            continue

    # 默认字符串比较
    return actual_str == expected_str


def _format_value(val: Any) -> str:
    """格式化值用于日志输出。"""
    if val is None:
        return "NULL"
    s = str(val)
    if len(s) > 100:
        return s[:97] + "..."
    return s


def _get_field_type(schema: dict, table: str, field: str) -> str:
    """从schema中获取字段类型。"""
    try:
        return (
            schema.get("tables", {})
            .get(table, {})
            .get(field, {})
            .get("type", "")
            .upper()
        )
    except Exception as e:
        _log.debug("  [DB-Verifier] 获取字段类型失败: %s", e)
        return ""


def _limit_clause(db_agent, n: int = 10) -> str:
    """根据当前数据库类型返回正确的行数限制子句。"""
    with guard("_limit_clause: db_type = getattr(db_agent, '_db_typ", log=_log):
        db_type = getattr(db_agent, "_db_type", "").lower()
        if db_type in ("yasdb", "oracle"):
            return f"FETCH FIRST {n} ROWS ONLY"
    return f"LIMIT {n}"


def _get_schema_context() -> str:
    """获取数据库表结构摘要，供 AI 生成 SQL 时参考。"""
    with guard("_get_schema_context: from src.core.session_context import", log=_log):
        from src.core.session_context import session_ctx

        ctx = session_ctx
        if ctx:
            schema_text = ctx._get_table_schema_summary()
            if schema_text:
                return schema_text
    return ""


def _quote_ident(db_agent, name: str) -> str:
    """根据数据库类型返回正确引用的标识符。"""
    try:
        db_type = getattr(db_agent, "_db_type", "").lower()
        if db_type == "mysql":
            return "`%s`" % name
        else:
            return '"%s"' % name
    except Exception as e:
        _log.debug("  [DB-Verifier] 获取数据库类型失败，使用默认双引号标识符: %s", e)
        return '"%s"' % name
