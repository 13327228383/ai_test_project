# -*- coding: utf-8 -*-
"""
_test_methods.py — 测试方法论清单 API 数据获取器

提供 /api/test-methods 的两类统计口径聚合：
- case_count：生成的用例数（test_cases.count 归纳到方法论编码）
- call_count：执行次数（executions JOIN test_cases 归纳到方法论编码）

按三桶聚合：system(全局) / project(当前项目) / version(当前项目+版本)。

方法论编码源：★ 根因修复(2026-09-20) — 单一权威来源 src/testing_methods/catalog.py（18 编码），
取代原先本文件硬编码的 12 码 METHOD_NAMES，消除与规则层/引擎类的口径分散。
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)

from ._utils import _db_conn, _current_project_id, _current_version_id

# ★ 单一权威目录：code -> (name, category, description)，与 _test_method_rules.py 同源
from src.testing_methods.catalog import (
    METHOD_CATALOG as METHOD_NAMES,
    METHOD_CODES,
    CATEGORY_LABELS,
)


def _aggregate(conn, table, method_col, scope):
    """按方法论编码聚合 case_count。

    table: "test_cases"（case口径）或 "executions"（call口径需JOIN test_cases）。
    method_col: test_cases 侧方法论列。
    scope: "system" / "project" / "version"。
    """
    project = _current_project_id()
    version_id = _current_version_id()

    where = []
    args = []
    if scope == "project" or scope == "version":
        if not project:
            return {}
        where.append("c.project=%s")
        args.append(project)
        if scope == "version" and version_id:
            where.append("c.version_id=%s")
            args.append(version_id)

    if table == "test_cases":
        sql = (
            f"SELECT c.test_method AS code, COUNT(*) AS n "
            f"FROM test_cases c"
            f"{' WHERE ' + ' AND '.join(where) if where else ''} "
            f"GROUP BY c.test_method"
        )
    else:  # executions JOIN test_cases
        base_where = ["tc.project=e.project", "tc.version_id=e.version_id", "tc.case_id=e.case_id"]
        base_where.extend(where)
        sql = (
            f"SELECT tc.test_method AS code, COUNT(*) AS n "
            f"FROM executions e JOIN test_cases tc "
            f"ON tc.project=e.project AND tc.version_id=e.version_id AND tc.case_id=e.case_id"
            f" WHERE " + " AND ".join(base_where) +
            f" GROUP BY tc.test_method"
        )

    out = {}
    with guard("_aggregate: rows = conn.execute(sql, tuple(args)", log=_log, level=logging.WARNING):
        rows = conn.execute(sql, tuple(args)).fetchall()
        for row in rows:
            code = str(row["code"] or "").strip().upper()
            if code in METHOD_NAMES:
                out[code] = int(row["n"] or 0)
    return out


def fetch_test_methods():
    """聚合三桶(系统/项目/版本)的 case_count 与 call_count。"""
    conn = _db_conn()
    project = _current_project_id()
    version_id = _current_version_id()
    try:
        counts = {
            "case": {"system": {}, "project": {}, "version": {}},
            "call": {"system": {}, "project": {}, "version": {}},
        }
        if conn is not None:
            counts["case"]["system"] = _aggregate(conn, "test_cases", "test_method", "system")
            counts["case"]["project"] = _aggregate(conn, "test_cases", "test_method", "project")
            counts["case"]["version"] = _aggregate(conn, "test_cases", "test_method", "version")
            counts["call"]["system"] = _aggregate(
                conn, "executions", "test_method", "system"
            )
            counts["call"]["project"] = _aggregate(
                conn, "executions", "test_method", "project"
            )
            counts["call"]["version"] = _aggregate(
                conn, "executions", "test_method", "version"
            )
        else:
            _log.warning("[test-methods] DB 连接不可用，返回全部 0 空桶")

        methods = []
        for code in METHOD_CODES:
            cname, cat, desc = METHOD_NAMES[code]
            methods.append(
                {
                    "code": code,
                    "name": cname,
                    "category": cat,
                    "category_label": CATEGORY_LABELS.get(cat, cat),
                    "description": desc,
                    "case_count": {
                        "system": counts["case"]["system"].get(code, 0),
                        "project": counts["case"]["project"].get(code, 0),
                        "version": counts["case"]["version"].get(code, 0),
                    },
                    "call_count": {
                        "system": counts["call"]["system"].get(code, 0),
                        "project": counts["call"]["project"].get(code, 0),
                        "version": counts["call"]["version"].get(code, 0),
                    },
                }
            )

        return {
            "ok": True,
            "context": {
                "project": project,
                "version_id": version_id,
                "scope_labels": {
                    "system": "全系统",
                    "project": "当前项目",
                    "version": "当前版本",
                },
            },
            "total": len(methods),
            "methods": methods,
        }
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[test-methods] 连接关闭异常: %s", _e, exc_info=True)