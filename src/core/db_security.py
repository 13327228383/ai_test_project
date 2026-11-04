# -*- coding: utf-8 -*-
"""SQL 安全模块 — 表名白名单 + 列名校验 — 从 db_manager 拆分而来。"""

import re
import threading
import logging

_log = logging.getLogger(__name__)

_TABLE_WHITELIST: set = None
_TABLE_WHITELIST_LOCK = threading.Lock()

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _load_table_whitelist(conn) -> set:
    """从 information_schema 动态加载当前数据库的全部表名作为白名单。"""
    try:
        rows = conn.fetchall_sql(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s",
            (conn.db.decode() if isinstance(conn.db, bytes) else conn.db,),
        )
        return {r["TABLE_NAME"] for r in rows} if rows else set()
    except Exception as e:
        _log.warning("DB安全白名单加载异常: %s", e, exc_info=True)
        return set()


def _get_table_whitelist() -> set:
    """惰性加载表名白名单（线程安全，仅在首次调用时加载）。"""
    global _TABLE_WHITELIST
    if _TABLE_WHITELIST is not None:
        return _TABLE_WHITELIST
    with _TABLE_WHITELIST_LOCK:
        if _TABLE_WHITELIST is not None:
            return _TABLE_WHITELIST
        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            _TABLE_WHITELIST = _load_table_whitelist(conn)
        except Exception:
            _TABLE_WHITELIST = set()
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)
        return _TABLE_WHITELIST


def execute_safe(conn, sql_template: str, table_names: list, params=None):
    """
    ★ P0-01: 安全 SQL 执行 — 表名必须通过白名单校验。
    """
    whitelist = _get_table_whitelist()
    for i, tn in enumerate(table_names):
        if not _IDENTIFIER_RE.match(tn):
            raise ValueError(f"SQL注入防护: 非法表名格式 '{tn}'")
        if whitelist and tn not in whitelist:
            raise ValueError(f"SQL注入防护: 表名 '{tn}' 不在白名单中，拒绝执行")
    sql = sql_template.format(*table_names)
    return conn.execute(sql, params)


# 允许的列名集合
_SAFE_COLUMN_PATTERN = re.compile(
    r"^(id|name|title|status|type|role|email|phone|password|"
    r"username|nickname|avatar|description|content|config|"
    r"created_at|updated_at|deleted_at|last_login|login_count|"
    r"is_active|is_admin|is_deleted|is_superuser|level|parent_id|"
    r"project|project_name|module|version|version_label|priority|"
    r"case_id|case_type|case_asset_file|req_id|run_id|step|step_idx|"
    r"confidence|confidence_score|risk_level|executed_at|verified_at|"
    r"[a-z_][a-z0-9_]{0,62})$"
)


def validate_column_name(col_name: str) -> bool:
    """校验动态列名是否安全。"""
    return bool(_SAFE_COLUMN_PATTERN.match(col_name))


def validate_column_names(col_names: list) -> None:
    """批量校验列名，任一不合法则抛出 ValueError。"""
    for cn in col_names:
        if not validate_column_name(cn):
            raise ValueError(f"SQL注入防护: 非法列名 '{cn}'")


def validate_table_name(name: str) -> bool:
    """校验表名，合法表名只含字母数字下划线，返回 True/False"""
    if not name or not isinstance(name, str):
        return False
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name))


# ★ 只读 SQL 校验：禁止所有写操作和 DDL
_READONLY_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|MERGE|"
    r"CALL|EXEC|EXECUTE|GRANT|REVOKE|LOCK|UNLOCK|FLUSH|RESET|SET|SHUTDOWN|"
    r"LOAD_FILE|OUTFILE|DUMPFILE)\b",
    re.IGNORECASE,
)


def validate_readonly_sql(sql: str) -> bool:
    """校验 SQL 是否为只读 SELECT 查询，禁止任何写操作 / DDL / 系统命令。

    规则：
      1. 必须以 SELECT 或 WITH 开头（CTE 语法）
      2. 不能包含 INSERT/UPDATE/DELETE/DROP/ALTER 等写关键字
      3. 不能包含子查询中的写操作
    """
    if not sql or not isinstance(sql, str):
        return False
    stripped = sql.strip()
    # 必须以 SELECT 或 WITH 开头（WITH...SELECT 是 CTE 语法）
    if not re.match(r"^(SELECT|WITH)\s", stripped, re.IGNORECASE):
        return False
    # 不能包含任何禁止关键字
    if _READONLY_FORBIDDEN_KEYWORDS.search(stripped):
        return False
    return True
