# -*- coding: utf-8 -*-
"""DB schema 自动加载 — 从 db_verifier.py 拆出，零逻辑变更。"""

from typing import Any
from src.core.guarded import guard
import logging

from .helpers import _quote_ident

_log = logging.getLogger(__name__)


def _auto_load_schema(db_agent, table_name: str = None) -> dict[str, Any]:
    """
    自动从数据库元数据加载表结构。
    不依赖 session_ctx，直接查询 information_schema。
    """
    schema = {"tables": {}, "loaded": False}
    with guard("_auto_load_schema: db_type = getattr(db_agent, '_db_typ", log=_log, level=logging.INFO):
        db_type = getattr(db_agent, "_db_type", "").lower()

        if db_type == "mysql":
            # MySQL: 查询 information_schema
            if table_name:
                sql = """
                    SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
                """
                result = db_agent.query(sql, (table_name,))
            else:
                sql = """
                    SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                """
                result = db_agent.query(sql)

        elif db_type in ("yasdb", "oracle"):
            # YasDB/Oracle: 查询 USER_TAB_COLUMNS
            if table_name:
                sql = """
                    SELECT COLUMN_NAME, DATA_TYPE, NULLABLE, DATA_DEFAULT
                    FROM USER_TAB_COLUMNS
                    WHERE TABLE_NAME = %s
                """ % _quote_ident(
                    db_agent, table_name
                )
                result = db_agent.query(sql)
            else:
                sql = """
                    SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
                    FROM USER_TAB_COLUMNS
                """
                result = db_agent.query(sql)

        elif db_type == "postgresql":
            # PostgreSQL
            if table_name:
                sql = """
                    SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = 'public' AND TABLE_NAME = %s
                """
                result = db_agent.query(sql, (table_name,))
            else:
                sql = """
                    SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = 'public'
                """
                result = db_agent.query(sql)

        elif db_type == "sqlite":
            # SQLite: PRAGMA table_info
            if table_name:
                sql = "PRAGMA table_info(%s)" % _quote_ident(db_agent, table_name)
                result = db_agent.query(sql)
            else:
                # SQLite 需要逐个表查询
                tables_result = db_agent.query(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
                schema["tables"]["_all_tables"] = [
                    r.get("name", r[0]) for r in tables_result.get("data", [])
                ]
                result = {"success": True, "data": []}

        else:
            return schema

        if result.get("success"):
            schema["loaded"] = True
            for row in result.get("data", []):
                if isinstance(row, dict):
                    tbl = row.get("TABLE_NAME", table_name or "unknown")
                    col = row.get("COLUMN_NAME", row.get("column_name", ""))
                    dtype = row.get("DATA_TYPE", row.get("data_type", ""))
                else:
                    # SQLite PRAGMA 返回 tuple
                    tbl = table_name or "unknown"
                    col = row[1] if len(row) > 1 else ""
                    dtype = row[2] if len(row) > 2 else ""

                if tbl not in schema["tables"]:
                    schema["tables"][tbl] = {}
                schema["tables"][tbl][col] = {
                    "type": dtype,
                    "nullable": row.get("IS_NULLABLE", row.get("NULLABLE", "YES")),
                }


    return schema
