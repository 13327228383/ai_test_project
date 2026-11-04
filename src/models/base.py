# -*- coding: utf-8 -*-
from __future__ import annotations
"""
基础 DB 工具 — 连接、通用操作
===============================
提供统一的数据库连接获取方式，以及通用查询/写入辅助。
连接由 src/core/db_manager.py 统一管理，表结构由 schema.sql 自动建表。
"""

import logging
_log = logging.getLogger(__name__)


def safe_add_column(c, table: str, col_name: str, col_def: str):
    """安全添加列：仅当列不存在时 ALTER TABLE ADD COLUMN。
    MySQL 模式下，Duplicate column 错误码为 1060。
    ★ P0-01: 表名和列名通过白名单/格式校验后再拼入 SQL。"""
    from src.core.db_manager import execute_safe, validate_column_name
    if not validate_column_name(col_name):
        _log.warning("[Models] 拒绝非法列名: %s", col_name)
        return
    try:
        execute_safe(c, "ALTER TABLE {0} ADD COLUMN {1} {2}", [table, col_name, col_def])
    except ValueError as ve:
        _log.warning("[Models] 表名/列名校验失败: %s", ve)
    except Exception as e:
        err_str = str(e).lower()
        if "duplicate" not in err_str and "1060" not in err_str:
            _log.info(f"[Models] ALTER TABLE {table} ADD {col_name} 失败: {e}")

def get_conn():
    """
    获取 MySQL 数据库连接。
    由 db_manager 管理连接池和自动 schema 初始化。
    """
    from src.core.db_manager import get_connection

    return get_connection()

def query_one(sql: str, params: tuple = ()) -> dict | None:
    """执行查询，返回单行字典或 None。"""
    c = None
    try:
        c = get_conn()
        row = c.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        if c:
            try: c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)
def query_all(sql: str, params: tuple = ()) -> list[dict]:
    """执行查询，返回字典列表。"""
    c = None
    try:
        c = get_conn()
        rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        if c:
            try: c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)
def execute(sql: str, params: tuple = ()) -> bool:
    """执行写操作，返回是否成功。"""
    c = None
    try:
        c = get_conn()
        c.execute(sql, params)
        c.commit()
        return True
    except Exception as e:
        _log.info(f"[Models] execute 失败: {e}")
        return False
    finally:
        if c:
            try: c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)
def execute_insert(sql: str, params: tuple = ()) -> int | None:
    """执行 INSERT 并返回 lastrowid。"""
    c = None
    try:
        c = get_conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid
    except Exception as e:
        _log.info(f"[Models] execute_insert 失败: {e}")
        return None
    finally:
        if c:
            try: c.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)