# -*- coding: utf-8 -*-
"""工具函数：线程连接追踪、连接释放、健康检查、启动 schema 初始化。"""

import threading
import logging

_log = logging.getLogger(__name__)

# ★ 线程安全：追踪当前线程借出的连接（供 teardown 兜底释放）
_tracked_connections = threading.local()


def _track_connection(conn):
    """将连接记录到当前线程的追踪列表。"""
    if not hasattr(_tracked_connections, "conns"):
        _tracked_connections.conns = []
    _tracked_connections.conns.append(conn)


def _untrack_connection(conn):
    """从当前线程的追踪列表移除连接。"""
    conns = getattr(_tracked_connections, "conns", None)
    if conns:
        try:
            conns.remove(conn)
        except ValueError as e:
            _log.debug("连接不在追踪列表中: %s", e)


def release_thread_connections():
    """释放当前线程所有未归还的连接（Flask teardown 兜底）。"""
    conns = getattr(_tracked_connections, "conns", None)
    if conns:
        for c in conns:
            try:
                c.close()
            except Exception as _e:
                _log.debug(
                    "read + release_thread_connections + close(): %s", _e, exc_info=True
                )
        conns.clear()


def release_connection(conn) -> None:
    """归还连接到池（等同于 conn.close()，PooledDB 会自动回收）。"""
    if conn is None:
        return
    try:
        _untrack_connection(conn)
        conn.close()
    except Exception as _e:
        _log.debug("release_connection + conn.close + close(): %s", _e, exc_info=True)


def init_schema_on_startup() -> None:
    """应用启动时预初始化 schema，避免首次 API 调用承担初始化延迟。"""
    from src.core.db_schema import _init_schema
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        _init_schema(conn)
    except Exception as e:
        _log.warning("[WARN] 启动时 schema 初始化失败: %s", e)
    finally:
        if conn:
            release_connection(conn)


def health_check() -> dict:
    """健康检查：借用连接 → ping → 归还。"""
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1 AS alive")
            r = cur.fetchone()
        finally:
            try:
                cur.close()
            except Exception as _e:
                _log.debug("execute + fetchone + close(): %s", _e, exc_info=True)
        return {"ok": bool(r and r["alive"] == 1), "error": ""}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    finally:
        if conn:
            release_connection(conn)
