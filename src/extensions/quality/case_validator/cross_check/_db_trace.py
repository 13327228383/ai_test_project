# -*- coding: utf-8 -*-
"""
Intent Validator - P0 防幻觉：DB 表/字段确定性校验 (v3.0)
==========================================================
从 checks[{track=db, precision=exact}].check.sql 提取表名/字段名，
与真实 DB schema（diff_snapshots db_schema）直接比对。

零 AI 调用 — 纯字符串解析 + 集合成员判断。

★ v3.0 关键变化:
  - 不再依赖 trace_links（需求→表映射，会被AI绕过去）
  - 直接查 DB schema 快照中的真实表名/列名
  - 表名不存在 → error（阻断级）
  - 提供自动修正建议（找最相似表名）
"""

import json
import logging
import re

from ._data import load_cross_check_data

_log = logging.getLogger(__name__)

# SQL 表名提取正则: FROM/JOIN/INTO/UPDATE table_name 和 INSERT INTO table_name
_SQL_TABLE_RE = re.compile(
    r"(?:FROM|JOIN|INTO|UPDATE)\s+`?(\w+)`?",
    re.IGNORECASE,
)
# SQL 列名提取（SELECT 子句）
_SQL_COLUMN_RE = re.compile(
    r"(?:WHERE|SET|ON|AND|OR)\s+`?(\w+)`?\s*[=<>!]",
    re.IGNORECASE,
)
# 也提取 SELECT 后的列名
_SQL_SELECT_COL_RE = re.compile(
    r"SELECT\s+(.+?)\s+FROM",
    re.IGNORECASE | re.DOTALL,
)


def cross_check_db_tables(cases: list[dict]) -> list[dict]:
    """
    v3.0 确定性校验：从 checks[track=db, precision=exact] 的 SQL 中提取
    表名/字段名，与真实 DB schema 比对。

    返回: [{"case_id": "...", "reason": "...", "severity": "error|warning", "cross_check": "db_trace", "fix_hint": "..."}]
    """
    data = load_cross_check_data()
    db_tables = data.get("db_tables", set())
    db_columns = data.get("db_columns", {})

    if not db_tables:
        return [
            {
                "case_id": "SYSTEM",
                "reason": "⚠️ DB Schema基准未加载（db_tables为空），DB表/字段确定性校验已跳过。建议执行Step1加载DB数据源。",
                "severity": "warning",
                "cross_check": "baseline",
            }
        ]

    errors = []

    for c in cases:
        if not isinstance(c, dict):
            continue
        cid = c.get("case_id", "?")
        checks = c.get("checks") or []
        if isinstance(checks, str):
            try:
                checks = json.loads(checks)
            except Exception:
                checks = []

        for ci, chk in enumerate(checks if isinstance(checks, list) else []):
            if not isinstance(chk, dict):
                continue
            if str(chk.get("track", "")).strip().lower() != "db":
                continue

            precision = str(chk.get("precision", "fuzzy")).strip()
            check_data = chk.get("check", "")

            # 只对 exact + dict 格式做硬校验（exact 必须有具体 SQL）
            if precision != "exact":
                continue

            sql = ""
            table_name = ""
            if isinstance(check_data, dict):
                sql = str(check_data.get("sql", ""))
                table_name = str(check_data.get("table", "")).strip()
            elif isinstance(check_data, str):
                sql = check_data

            if not sql and not table_name:
                continue

            # ── 1. 提取并校验表名 ──
            if table_name:
                tables_found = [table_name]
            else:
                tables_found = _SQL_TABLE_RE.findall(sql)

            for tbl in tables_found:
                tbl_lower = tbl.lower().strip("`\"' ")
                if not tbl_lower:
                    continue
                if tbl_lower not in db_tables:
                    suggestion = _find_closest_table(tbl_lower, db_tables)
                    errors.append(
                        {
                            "case_id": cid,
                            "reason": (
                                f"checks[{ci}] SQL引用的表「{tbl}」在数据库schema中不存在。"
                                f"{'建议替换为: ' + suggestion if suggestion else '请确认表名是否正确。'}"
                            ),
                            "severity": "error",  # ★ 编造表名 → 阻断级
                            "cross_check": "db_trace",
                            "fix_hint": suggestion,
                            "bad_table": tbl_lower,
                        }
                    )

            # ── 2. 提取并校验列名 ──
            if not sql:
                continue
            columns_found = _SQL_COLUMN_RE.findall(sql)
            # 也提取 SELECT 列
            select_match = _SQL_SELECT_COL_RE.search(sql)
            if select_match:
                select_cols = select_match.group(1)
                for col in select_cols.split(","):
                    col = col.strip()
                    # 去掉别名（AS xxx）
                    as_match = re.search(r"(?:AS\s+)?(\w+)$", col, re.IGNORECASE)
                    if as_match:
                        col = as_match.group(1)
                    col = col.strip("`\"'* ")
                    if (
                        col
                        and not col.isdigit()
                        and col.lower() not in ("count", "sum", "avg", "max", "min")
                    ):
                        columns_found.append(col)

            # 找表对应的列集合
            for tbl_candidate in tables_found[:1]:  # 主要检查第一个表的列
                tbl_lower = tbl_candidate.lower().strip("`\"' ")
                real_columns = db_columns.get(tbl_lower, set())
                if not real_columns:
                    continue
                for col in columns_found:
                    col_lower = col.lower().strip("`\"' ")
                    if not col_lower or col_lower == "*":
                        continue
                    # 排除 SQL 关键字
                    if col_lower in (
                        "where",
                        "and",
                        "or",
                        "set",
                        "on",
                        "null",
                        "not",
                        "in",
                        "like",
                        "between",
                    ):
                        continue
                    if col_lower not in real_columns:
                        suggestion = _find_closest_column(col_lower, real_columns)
                        errors.append(
                            {
                                "case_id": cid,
                                "reason": (
                                    f"checks[{ci}] SQL中引用的列「{col}」在表「{tbl_candidate}」中不存在。"
                                    f"{'建议替换为: ' + suggestion if suggestion else '请确认列名是否正确。'}"
                                ),
                                "severity": "error",
                                "cross_check": "db_trace",
                                "fix_hint": suggestion,
                                "bad_column": col_lower,
                                "bad_table": tbl_lower,
                            }
                        )

    return errors


def _find_closest_table(target: str, candidates: set) -> str:
    """找最相似表名（编辑距离 / 前缀匹配）。"""
    if not candidates:
        return ""
    target_lower = target.lower()
    # 优先前缀匹配
    for cand in sorted(candidates):
        if cand.startswith(target_lower) or target_lower.startswith(cand):
            return cand
    # 子串包含
    for cand in sorted(candidates):
        if target_lower in cand or cand in target_lower:
            return cand
    # 字符集合 Jaccard 相似度最高者
    best = ""
    best_score = 0
    target_set = set(target_lower)
    for cand in candidates:
        cand_set = set(cand)
        if not cand_set:
            continue
        score = len(target_set & cand_set) / len(target_set | cand_set)
        if score > best_score:
            best_score = score
            best = cand
    return best if best_score > 0.4 else ""


def _find_closest_column(target: str, candidates: set) -> str:
    """找最相似列名。"""
    return _find_closest_table(target, candidates)
