# -*- coding: utf-8 -*-
from __future__ import annotations

"""DB 扩展断言 — db_record_count_check / db_record_not_exists_check。

扩展 db_verify 的能力：直接断言记录数量和记录不存在。
通过 SQL 查询 COUNT 和 EXISTS 实现。
"""

import asyncio
import logging
from typing import Any

_log = logging.getLogger(__name__)


def _db_env_skip(result: dict) -> dict | None:
    """★ Fix F(2026-09-27)：连接级环境问题（skipped）→ 返回 UNKNOWN 轨，不参与 AND。

    根因：`verify_db` 连接失败时返回 `{"success": None, "skipped": True}`，而各 `_check_*`
    仅判 `not result.get("success")` → skipped 被当 FAIL，单轨环境问题拖垮整用例 AND（假 FAIL）。
    通用性：任何 DB 类型/靶子的连接级环境问题都跳过；仅 SQL 执行阶段失败仍判 FAIL。
    """
    if not isinstance(result, dict) or not result.get("skipped"):
        return None
    return {
        "passed": None,
        "skipped": True,
        "status": "UNKNOWN",
        "evidence": "DB轨跳过(环境问题): %s" % str(result.get("evidence", ""))[:80],
    }


def _param_unresolved(evidence: str) -> dict:
    """检查参数不可解析（缺参/非法表名）→ 引擎不可评估 → 非判定态（与 _db_env_skip 同语义）。"""
    return {
        "passed": None,
        "skipped": True,
        "status": "UNKNOWN",
        "evidence": "%s（引擎不可评估，非产品缺陷）" % evidence[:120],
    }


def _compare_values(actual, expected, operator: str):
    """公共比较函数：eq/gte/lte/gt/lt 数值比较，供 DB/API 断言共用。"""
    _OP_DISPATCH = {
        "eq": lambda a, e: a == e,
        "gte": lambda a, e: a >= e,
        "lte": lambda a, e: a <= e,
        "gt": lambda a, e: a > e,
        "lt": lambda a, e: a < e,
    }
    _cmp = _OP_DISPATCH.get(operator, _OP_DISPATCH["eq"])
    return _cmp(actual, expected)


async def verify_db_ext(check: dict[str, Any]) -> dict[str, Any]:
    """DB 扩展断言分派入口。

    check 格式：
      db_record_count_check:
        {"type": "db_record_count_check",
         "table": "users",
         "where": "status='active'",
         "expected_count": 5,
         "operator": "eq|gte|lte|gt|lt"}
      db_record_not_exists_check:
        {"type": "db_record_not_exists_check",
         "table": "users",
         "where": "id=999"}
    """
    check_type = check.get("type", "")

    try:
        if check_type == "db_record_count_check":
            return await asyncio.to_thread(_check_record_count, check)
        elif check_type == "db_record_not_exists_check":
            return await asyncio.to_thread(_check_record_not_exists, check)
        elif check_type == "db_record_exists_check":
            return await asyncio.to_thread(_check_record_exists, check)
        elif check_type == "db_field_value_check":
            return await asyncio.to_thread(_check_field_value, check)
        elif check_type == "db_query_check":
            return await asyncio.to_thread(_check_query, check)
        else:
            # ★ 未知类型视为执行器异常（阻断），不再 silently pass
            return {
                "passed": False,
                "error": "unknown_check_type",
                "evidence": "[执行器异常] 未知DB断言类型: %s" % check_type,
            }
    except Exception as e:
        _log.error("[DbExtCheck] 异常: %s", str(e)[:120], exc_info=True)
        # ★ 异常视为执行器故障，断言失败（避免假通过）
        return {
            "passed": False,
            "error": "executor_exception",
            "evidence": "[执行器异常] DB断言崩溃: %s" % str(e)[:80],
        }


def _check_record_count(check: dict[str, Any]) -> dict[str, Any]:
    """检查数据库记录数量。"""
    from src.pipeline.steps.step4.db_verifier import verify_db
    from src.core.db_security import validate_table_name

    table = check.get("table", "")
    where = check.get("where", "")
    expected_count = check.get("expected_count", 0)
    operator = check.get("operator", "eq")

    if not table:
        return _param_unresolved("DB[count] 缺少 table 参数")
    if not validate_table_name(table):
        return _param_unresolved("DB[count] 非法表名: %s" % table)

    sql = "SELECT COUNT(*) AS cnt FROM %s" % table
    if where:
        sql += " WHERE %s" % where

    result = verify_db(sql, use_ai=False)
    _skip = _db_env_skip(result)
    if _skip is not None:
        return _skip
    if not result.get("success"):
        return {
            "passed": False,
            "evidence": "DB[count] 查询失败: %s" % result.get("evidence", "")[:80],
        }

    rows = result.get("data", [])
    actual_count = rows[0].get("cnt", 0) if rows else 0

    passed = _compare_values(actual_count, expected_count, operator)

    return {
        "passed": passed,
        "evidence": "DB[count] %s WHERE %s → %d (期望 %s %d)"
        % (
            table,
            where or "1=1",
            actual_count,
            operator,
            expected_count,
        ),
    }


def _check_record_not_exists(check: dict[str, Any]) -> dict[str, Any]:
    """检查数据库记录不存在。"""
    from src.pipeline.steps.step4.db_verifier import verify_db
    from src.core.db_security import validate_table_name

    table = check.get("table", "")
    where = check.get("where", "")

    if not table or not where:
        return _param_unresolved("DB[not_exists] 缺少 table 或 where 参数")
    if not validate_table_name(table):
        return _param_unresolved("DB[not_exists] 非法表名: %s" % table)

    sql = "SELECT COUNT(*) AS cnt FROM %s WHERE %s" % (table, where)

    result = verify_db(sql, use_ai=False)
    _skip = _db_env_skip(result)
    if _skip is not None:
        return _skip
    if not result.get("success"):
        return {
            "passed": False,
            "evidence": "DB[not_exists] 查询失败: %s" % result.get("evidence", "")[:80],
        }

    rows = result.get("data", [])
    actual_count = rows[0].get("cnt", 0) if rows else 0
    passed = actual_count == 0

    return {
        "passed": passed,
        "evidence": "DB[not_exists] %s WHERE %s → %d 条记录（期望0）"
        % (
            table,
            where,
            actual_count,
        ),
    }


def _check_record_exists(check: dict[str, Any]) -> dict[str, Any]:
    """检查数据库记录存在（与 not_exists 对称）。"""
    from src.pipeline.steps.step4.db_verifier import verify_db
    from src.core.db_security import validate_table_name

    table = check.get("table", "")
    where = check.get("where", "")

    if not table:
        return _param_unresolved("DB[exists] 缺少 table 参数")
    if not validate_table_name(table):
        return _param_unresolved("DB[exists] 非法表名: %s" % table)

    sql = "SELECT COUNT(*) AS cnt FROM %s" % table
    if where:
        sql += " WHERE %s" % where

    result = verify_db(sql, use_ai=False)
    _skip = _db_env_skip(result)
    if _skip is not None:
        return _skip
    if not result.get("success"):
        return {
            "passed": False,
            "evidence": "DB[exists] 查询失败: %s" % result.get("evidence", "")[:80],
        }

    rows = result.get("data", [])
    actual_count = rows[0].get("cnt", 0) if rows else 0
    passed = actual_count > 0

    return {
        "passed": passed,
        "evidence": "DB[exists] %s WHERE %s → %d 条记录（期望>0）"
        % (
            table,
            where,
            actual_count,
        ),
    }


def _check_field_value(check: dict[str, Any]) -> dict[str, Any]:
    """检查数据库某字段值，支持 operator（eq/gte/lte/gt/lt/in/range/neq）和 tolerance（精度容差）。

    check 格式：
      {"type": "db_field_value_check",
       "table": "orders", "field": "amount", "where": "id=1",
       "expected": 99.50,                        # 期望值
       "operator": "eq",                         # 默认 eq
       "tolerance": 0.02}                        # 仅 eq 时生效，|actual - expected| <= tolerance 视为相等
    """
    from src.pipeline.steps.step4.db_verifier import verify_db
    from src.core.db_security import validate_table_name, validate_column_name

    table = check.get("table", "")
    field = check.get("field", "")
    where = check.get("where", "")
    expected = check.get("expected", "")
    operator = (check.get("operator") or "eq").strip().lower()
    tolerance = float(check.get("tolerance") or 0)

    if not table or not field or not where:
        return _param_unresolved("DB[field] 缺少 table/field/where 参数")
    if not validate_table_name(table):
        return _param_unresolved("DB[field] 非法表名: %s" % table)
    if not validate_column_name(field):
        return _param_unresolved("DB[field] 非法字段名: %s" % field)

    sql = "SELECT %s AS fval FROM %s WHERE %s LIMIT 1" % (field, table, where)
    result = verify_db(sql, use_ai=False)
    _skip = _db_env_skip(result)
    if _skip is not None:
        return _skip
    if not result.get("success"):
        return {
            "passed": False,
            "evidence": "DB[field] 查询失败: %s" % result.get("evidence", "")[:80],
        }

    rows = result.get("data", [])
    if not rows:
        return {
            "passed": False,
            "evidence": "DB[field] %s WHERE %s 无匹配记录" % (table, where),
        }

    actual_value = rows[0].get("fval", "")

    # 类型自适应：尝试数值比较，失败回退字符串比较
    try:
        actual_num = float(actual_value)
        expected_num = float(expected)
        is_numeric = True
    except (TypeError, ValueError):
        is_numeric = False

    if operator == "eq":
        if is_numeric and tolerance > 0:
            passed = abs(actual_num - expected_num) <= tolerance
            evidence_detail = "数值精度比较 |%g - %g| = %g ≤ tolerance=%g" % (
                actual_num,
                expected_num,
                abs(actual_num - expected_num),
                tolerance,
            )
        elif is_numeric:
            passed = actual_num == expected_num
            evidence_detail = "数值严格相等"
        else:
            passed = str(actual_value) == str(expected)
            evidence_detail = "字符串严格相等"
    elif operator == "neq":
        passed = str(actual_value) != str(expected)
        evidence_detail = "字符串不等"
    elif operator in ("gte", "lte", "gt", "lt"):
        if not is_numeric:
            return {
                "passed": False,
                "evidence": "DB[field] operator=%s 需要数值类型，实际=%s"
                % (operator, actual_value),
            }
        passed = _compare_values(actual_num, expected_num, operator)
        evidence_detail = "数值比较 %g %s %g" % (actual_num, operator, expected_num)
    elif operator == "in":
        # expected 应为逗号分隔字符串或列表
        if isinstance(expected, str):
            expected_list = [x.strip() for x in expected.split(",")]
        elif isinstance(expected, list):
            expected_list = [str(x) for x in expected]
        else:
            expected_list = [str(expected)]
        passed = str(actual_value) in expected_list
        evidence_detail = "集合包含 actual=%s in %s" % (actual_value, expected_list)
    elif operator == "range":
        # expected 应为 "min,max" 形式
        # ★ ADI tail  [已复核-DeepSeek-V3 ]: 修复 range 在
        #   "min,max" 字符串下不可达的 bug。原实现先 float(expected)（"10,50" 抛
        #   ValueError → is_numeric=False），range 分支要求 is_numeric 恒不满足 → range
        #   永远 FAIL。改为在 range 分支内独立解析 min/max 与实际值。
        if not isinstance(expected, str) or "," not in expected:
            return {
                "passed": False,
                "evidence": "DB[field] range 需要 expected='min,max' 且为数值",
            }
        parts = expected.split(",")
        try:
            min_v, max_v = float(parts[0]), float(parts[1])
            actual_num_r = float(actual_value)
            passed = min_v <= actual_num_r <= max_v
            evidence_detail = "范围比较 %g ≤ %g ≤ %g" % (min_v, actual_num_r, max_v)
        except (ValueError, IndexError):
            return {
                "passed": False,
                "evidence": "DB[field] range expected 格式错误: %s" % expected,
            }
    else:
        return {"passed": False, "evidence": "DB[field] 未知 operator: %s" % operator}

    return {
        "passed": passed,
        "evidence": "DB[field] %s.%s WHERE %s = '%s' (期望 %s %s) → %s | %s"
        % (
            table,
            field,
            where,
            actual_value,
            operator,
            expected,
            "PASS" if passed else "FAIL",
            evidence_detail,
        ),
    }


def _check_query(check: dict[str, Any]) -> dict[str, Any]:
    """自定义只读 SQL 查询断言，支持跨表 JOIN / SUM / AVG / COUNT 等聚合运算。

    check 格式：
      {"type": "db_query_check",
       "sql": "SELECT SUM(amount) AS total FROM orders WHERE user_id=1",
       "expected": 999.50,
       "operator": "eq",              # eq/gte/lte/gt/lt/in/range/neq
       "tolerance": 0.02,             # eq 时数值精度容差
       "result_field": "total"}       # 从结果集哪个字段取值（默认取第一行第一列）
    """
    from src.pipeline.steps.step4.db_verifier import verify_db
    from src.core.db_security import validate_readonly_sql

    sql = check.get("sql", "").strip()
    expected = check.get("expected", "")
    operator = (check.get("operator") or "eq").strip().lower()
    tolerance = float(check.get("tolerance") or 0)
    result_field = check.get("result_field") or ""

    if not sql:
        return _param_unresolved("DB[query] 缺少 sql 参数")
    # ★ 安全校验：只允许 SELECT，禁止 INSERT/UPDATE/DELETE/DROP/ALTER 等
    if not validate_readonly_sql(sql):
        return _param_unresolved("DB[query] 非只读 SQL 或包含禁止关键字: %s" % sql[:80])

    result = verify_db(sql, use_ai=False)
    _skip = _db_env_skip(result)
    if _skip is not None:
        return _skip
    if not result.get("success"):
        return {
            "passed": False,
            "evidence": "DB[query] 查询失败: %s" % result.get("evidence", "")[:80],
        }

    rows = result.get("data", [])
    if not rows:
        return {"passed": False, "evidence": "DB[query] 查询无结果: %s" % sql[:80]}

    # 取值：优先 result_field，否则取第一行的第一个字段
    first_row = rows[0]
    if isinstance(first_row, dict):
        if result_field:
            actual_value = first_row.get(result_field)
        else:
            actual_value = next(iter(first_row.values())) if first_row else None
    else:
        actual_value = first_row[0] if first_row else None

    if actual_value is None:
        return {"passed": False, "evidence": "DB[query] 结果为 NULL: %s" % sql[:80]}

    # 数值类型自适应
    try:
        actual_num = float(actual_value)
        expected_num = float(expected)
        is_numeric = True
    except (TypeError, ValueError):
        is_numeric = False

    if operator == "eq":
        if is_numeric and tolerance > 0:
            passed = abs(actual_num - expected_num) <= tolerance
            evidence_detail = "数值精度 |%g - %g| ≤ %g" % (
                actual_num,
                expected_num,
                tolerance,
            )
        elif is_numeric:
            passed = actual_num == expected_num
            evidence_detail = "数值严格相等"
        else:
            passed = str(actual_value) == str(expected)
            evidence_detail = "字符串严格相等"
    elif operator == "neq":
        passed = str(actual_value) != str(expected)
        evidence_detail = "字符串不等"
    elif operator in ("gte", "lte", "gt", "lt"):
        if not is_numeric:
            return {
                "passed": False,
                "evidence": "DB[query] operator=%s 需要数值类型，实际=%s"
                % (operator, actual_value),
            }
        passed = _compare_values(actual_num, expected_num, operator)
        evidence_detail = "数值比较 %g %s %g" % (actual_num, operator, expected_num)
    elif operator == "range":
        # ★ ADI tail (2026-08-15) [已复核-DeepSeek-V3 2026-08-16]: 同 _check_field_value
        #   修复——range 在 "min,max" 字符串下原不可达（顶部 float(expected) 抛错），
        #   改为在 range 分支内独立解析 min/max 与实际值。
        if not isinstance(expected, str) or "," not in expected:
            return {
                "passed": False,
                "evidence": "DB[query] range 需要 expected='min,max' 且为数值",
            }
        parts = expected.split(",")
        try:
            min_v, max_v = float(parts[0]), float(parts[1])
            actual_num_r = float(actual_value)
            passed = min_v <= actual_num_r <= max_v
            evidence_detail = "范围比较 %g ≤ %g ≤ %g" % (min_v, actual_num_r, max_v)
        except (ValueError, IndexError):
            return {
                "passed": False,
                "evidence": "DB[query] range expected 格式错误: %s" % expected,
            }
    elif operator == "in":
        if isinstance(expected, str):
            expected_list = [x.strip() for x in expected.split(",")]
        elif isinstance(expected, list):
            expected_list = [str(x) for x in expected]
        else:
            expected_list = [str(expected)]
        passed = str(actual_value) in expected_list
        evidence_detail = "集合包含 actual=%s in %s" % (actual_value, expected_list)
    else:
        return {"passed": False, "evidence": "DB[query] 未知 operator: %s" % operator}

    return {
        "passed": passed,
        "evidence": "DB[query] SQL=%s → %s (期望 %s %s) → %s | %s"
        % (
            sql[:60],
            actual_value,
            operator,
            expected,
            "PASS" if passed else "FAIL",
            evidence_detail,
        ),
    }
