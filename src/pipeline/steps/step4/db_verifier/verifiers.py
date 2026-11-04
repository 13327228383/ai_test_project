# -*- coding: utf-8 -*-
"""具体校验器（structured / sql / natural / ai） — 从 db_verifier.py 拆出，零逻辑变更。"""

import re
from typing import Any
import logging

from .helpers import (
    _compare_with_precision,
    _format_value,
    _limit_clause,
    _get_schema_context,
    _quote_ident,
)
from .schema import _auto_load_schema
from .safety import _validate_sql_safe, _validate_where_safe

_log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# AI 驱动验证
# ══════════════════════════════════════════════════════════


def _verify_db_ai(db_agent, desc: str) -> dict[str, Any]:
    """
    AI 驱动的数据库验证：将自然语言意图交给 AI 生成 SQL 并执行。
    ★ P1-3: AI 生成 SQL 时传入自动加载的 schema。
    """
    try:
        from src.ai.ai_client import ai_brain

        # ★ P1-3: 优先使用自动加载的 schema
        schema_auto = _auto_load_schema(db_agent)
        schema_ctx = _get_schema_context()

        # 合并 schema 信息
        schema_parts = []
        if schema_ctx:
            schema_parts.append(schema_ctx)
        if schema_auto.get("loaded"):
            schema_parts.append("\n## 自动检测的表结构\n")
            for tbl, cols in schema_auto.get("tables", {}).items():
                if not tbl.startswith("_"):
                    col_info = ", ".join(
                        [
                            "%s(%s)" % (c, info.get("type", ""))
                            for c, info in cols.items()
                        ]
                    )
                    schema_parts.append("- %s: %s" % (tbl, col_info))
        combined_schema = (
            "\n".join(schema_parts)
            if schema_parts
            else "（无表结构信息，请根据常识推断表名和字段名）"
        )

        system_prompt = (
            "你是一名数据库查询专家。根据用户提供的「自然语言测试用例」和「表结构摘要」，"
            "生成一个精确的 SELECT 查询来验证数据。\n\n"
            "## 规则\n"
            "1. 只生成 SELECT 语句，禁止 INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE\n"
            "2. 如果意图涉及多表关联，使用 JOIN\n"
            "3. 优先使用 COUNT(*) 来验证记录数量，除非意图明确要求查具体字段值\n"
            "4. 字符串比较使用单引号，LIKE 用于模糊匹配\n"
            "5. 生成的 SQL 必须对 YasDB/Oracle 数据库有效（用 FETCH FIRST 替代 LIMIT）\n"
            "6. 输出纯 SQL 语句，不要包裹在 markdown 代码块中，不要加任何解释\n"
            "7. 不要生成多语句（不要用分号分隔多条SQL）\n\n"
            "## 表结构摘要\n"
            "%s\n" % combined_schema
        )

        user_message = "测试用例：%s\n请生成 SELECT 查询。" % desc

        ai_result = ai_brain._call_api(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.0,
            max_tokens=2048,
            caller_name="DB验证-SQL生成",
            task_type="operation_instruction",
        )

        if not ai_result.get("success"):
            return {
                "success": False,
                "evidence": "AI生成SQL失败: %s" % ai_result.get("error", ""),
                "data": [],
                "count": 0,
            }

        sql = ai_result["data"].strip()
        # 去除可能的 markdown 代码块包裹
        if sql.startswith("```"):
            lines = sql.split("\n")
            sql = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        sql = sql.strip().rstrip(";")

        _log.info("  [DB-AI] 生成SQL: %s", sql[:200])

        # ★ 安全修复：执行前校验 SQL 是否仅包含 SELECT
        is_safe, reason = _validate_sql_safe(sql)
        if not is_safe:
            return {
                "success": False,
                "evidence": "SQL安全校验失败: %s | SQL: %s" % (reason, sql[:100]),
                "data": [],
                "count": 0,
            }

        # 执行 SQL
        result = db_agent.query(sql)
        if not result.get("success"):
            return {
                "success": False,
                "evidence": "AI生成SQL执行失败: %s | SQL: %s"
                % (result.get("error", ""), sql[:100]),
                "data": [],
                "count": 0,
            }

        data = result.get("data", [])
        count = len(data)
        if data and len(data) > 0 and len(data[0]) == 1:
            # 单值结果（如 COUNT）：取第一个值
            first_val = (
                list(data[0].values())[0] if isinstance(data[0], dict) else data[0]
            )
            count = int(first_val) if first_val is not None else 0

        passed = count > 0
        evidence = "DB-AI验证: %s → %d条 → %s" % (
            desc[:60],
            count,
            "PASS" if passed else "FAIL",
        )
        return {"success": passed, "evidence": evidence, "data": data, "count": count}

    except Exception as e:
        return {
            "success": False,
            "evidence": "DB-AI验证异常: %s" % e,
            "data": [],
            "count": 0,
        }


# ══════════════════════════════════════════════════════════
# ★ P1-2: 结构化校验（增强版，支持字段级值校验）
# ══════════════════════════════════════════════════════════


def _verify_structured(db_agent, check: dict) -> dict[str, Any]:
    """
    结构化校验 — ★ P1 增强版。

    支持格式：
    {
        "table": "orders",
        "where": "order_id = '123'",
        "expect": ">=1",           # 记录数预期（可选）
        "field_check": {            # ★ P1-2 新增：字段级值校验
            "amount": {"expect": 100.00, "precision": 0.01},
            "status": {"expect": "paid"},
            "created_at": {"expect": "2024-01-01", "type": "date"}
        }
    }
    """
    table = check.get("table", "")
    # ★ 缺少 table 字段时返回明确错误，不生成无效 SQL
    if not table or not table.strip():
        return {
            "success": False,
            "evidence": "DB结构化校验失败: 缺少必需字段 'table'，无法生成SQL",
            "data": [],
            "count": 0,
        }
    table = table.strip()
    where = check.get("where", "1=1")
    expect = check.get("expect", ">=1")
    field_check = check.get("field_check", {})  # ★ P1-2

    # ★ P0 安全修复：校验 WHERE 子句，防止 SQL 注入
    where_safe, where_reason = _validate_where_safe(where)
    if not where_safe:
        return {
            "success": False,
            "evidence": "WHERE子句不安全: %s" % where_reason,
            "data": [],
            "count": 0,
        }

    # ★ P1-3: 自动加载 schema
    schema = _auto_load_schema(db_agent, table) if table else {}

    lim = _limit_clause(db_agent)
    qtable = _quote_ident(db_agent, table) if table else table
    sql = "SELECT * FROM %s WHERE %s %s" % (qtable, where, lim)
    result = db_agent.query(sql)
    if not result.get("success"):
        return {
            "success": False,
            "evidence": "SQL失败: %s" % result.get("error", ""),
            "data": [],
            "count": 0,
        }

    data = result.get("data", [])
    count = len(data)

    # 记录数校验
    expect_clean = expect.replace(" ", "")
    if ">=" in expect_clean:
        count_passed = count >= int(expect_clean.replace(">=", ""))
    elif "<=" in expect_clean:
        count_passed = count <= int(expect_clean.replace("<=", ""))
    elif ">" in expect_clean:
        count_passed = count > int(expect_clean.replace(">", ""))
    elif "<" in expect_clean:
        count_passed = count < int(expect_clean.replace("<", ""))
    elif "=" in expect_clean:
        count_passed = count == int(expect_clean.replace("=", ""))
    else:
        count_passed = count > 0

    # ★ P1-2: 字段级值校验
    field_results = []
    field_all_passed = True
    if field_check and data:
        for row in data:
            for field, check_cfg in field_check.items():
                actual_val = row.get(field) if isinstance(row, dict) else None
                expected_val = check_cfg.get("expect")
                precision = check_cfg.get("precision")

                field_passed = _compare_with_precision(
                    actual_val, expected_val, precision
                )

                if not field_passed:
                    field_all_passed = False
                    field_results.append(
                        "%s: 实际=%s, 预期=%s"
                        % (
                            field,
                            _format_value(actual_val),
                            _format_value(expected_val),
                        )
                    )

    # 综合结果
    all_passed = count_passed and field_all_passed
    evidence_parts = ["表%s 查到%d条(预期%s)" % (table, count, expect)]
    if not count_passed:
        evidence_parts.append("记录数不匹配")
    if field_results:
        evidence_parts.append("字段校验失败: %s" % "; ".join(field_results[:3]))
        if len(field_results) > 3:
            evidence_parts.append("...等%d项" % len(field_results))

    evidence = " → ".join(evidence_parts) + " → %s" % ("PASS" if all_passed else "FAIL")
    return {"success": all_passed, "evidence": evidence, "data": data, "count": count}


# ══════════════════════════════════════════════════════════
# SQL 直接校验
# ══════════════════════════════════════════════════════════


def _verify_sql(db_agent, sql: str) -> dict[str, Any]:
    is_safe, reason = _validate_sql_safe(sql)
    if not is_safe:
        return {
            "success": False,
            "evidence": "SQL安全校验失败: %s | SQL: %s" % (reason, sql[:100]),
            "data": [],
            "count": 0,
        }
    result = db_agent.query(sql)
    if not result.get("success"):
        return {
            "success": False,
            "evidence": "SQL失败: %s" % result.get("error", ""),
            "data": [],
            "count": 0,
        }
    data = result.get("data", [])
    count = len(data)
    passed = count > 0
    return {
        "success": passed,
        "evidence": "SQL查到%d条→%s" % (count, "PASS" if passed else "FAIL"),
        "data": data,
        "count": count,
    }


# ══════════════════════════════════════════════════════════
# ★ P1-4: 自然语言校验（增强正则解析）
# ══════════════════════════════════════════════════════════


def _verify_natural(db_agent, desc: str) -> dict[str, Any]:
    """
    自然语言解析 — ★ P1-4 增强版。
    支持更多条件模式：=, >, <, >=, <=, LIKE, IN 等。
    """
    desc_lower = desc.lower()
    table_match = re.search(r"(\w+)\s*表", desc)
    table = table_match.group(1) if table_match else ""
    where = "1=1"
    params = []

    # ★ P1-4: 增强的条件解析模式
    patterns = [
        # username = 'xxx' / name = 'xxx'
        (r"(\w+)\s*=\s*'([^']+)'", "%s = %s", True),
        # age = 18 / id = 123
        (r"(\w+)\s*=\s*(\d+)", "%s = %s", False),
        # age > 18 / amount >= 100.5
        (r"(\w+)\s*(>=|<=|>|<)\s*(\d+\.?\d*)", "%s %s %s", False),
        # name LIKE '张%'
        (r"(\w+)\s+like\s+'([^']+)'", "%s LIKE %s", True),
        # status IN ('a', 'b')
        (r"(\w+)\s+in\s+\(([^)]+)\)", "%s IN (%s)", True),
    ]

    conditions = []
    for pattern, template, is_string in patterns:
        for m in re.finditer(pattern, desc_lower):
            groups = m.groups()
            if len(groups) == 2:
                field, val = groups
                if is_string:
                    conditions.append((template % (field, "%s"), val))
                else:
                    conditions.append((template % (field, val), None))
            elif len(groups) == 3:
                field, op, val = groups
                conditions.append((template % (field, op, val), None))

    if conditions:
        # 组合多个条件
        cond_sqls = []
        for cond_sql, param in conditions:
            if param is not None:
                cond_sqls.append(cond_sql)
                params.append(param)
            else:
                cond_sqls.append(cond_sql)
        where = " AND ".join(cond_sqls)

    if not table:
        return {
            "success": False,
            "evidence": "无法解析表名: %s" % desc,
            "data": [],
            "count": 0,
        }

    lim = _limit_clause(db_agent)
    qtable = _quote_ident(db_agent, table)
    sql = "SELECT * FROM %s WHERE %s %s" % (qtable, where, lim)

    try:
        if params:
            result = db_agent.query(sql, params)
        else:
            result = db_agent.query(sql)
    except Exception as e:
        _log.debug("  [DB-Verifier] 参数化查询失败，回退到引号转义: %s", e)
        # 回退：如果 db_agent.query 不支持参数，使用引号转义
        safe_params = [p.replace("'", "''") for p in params if p]
        if safe_params:
            # 替换 %s 为实际值
            fallback_where = where
            for p in safe_params:
                fallback_where = fallback_where.replace("%s", "'%s'" % p, 1)
            sql_fallback = "SELECT * FROM %s WHERE %s %s" % (
                qtable,
                fallback_where,
                lim,
            )
        else:
            sql_fallback = sql
        result = db_agent.query(sql_fallback)

    return (
        result
        if result.get("success")
        else {
            "success": False,
            "evidence": "SQL失败: %s" % result.get("error", ""),
            "data": [],
            "count": 0,
        }
    )
