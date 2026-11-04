# -*- coding: utf-8 -*-
"""连接包装器：_PooledConnectionWrapper + _AutoCloseCursor。"""

import logging

_log = logging.getLogger(__name__)


class _PooledConnectionWrapper:
    """包装 PooledDB 连接，使得自定义的 execute/fetchone_sql 等方法可正常使用。

    DBUtils 的 SteadyDBConnection 不代理 __getattr__，导致 _open() 中注入的
    自定义方法无法通过代理链访问。此包装器在 PooledDB 连接之上添加这些方法。
    """

    __slots__ = ("_conn",)

    def __init__(self, pooled_conn):
        object.__setattr__(self, "_conn", pooled_conn)

    # ── 透传标准 DB-API 属性 ──
    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name == "_conn":
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)

    # ── 自定义便捷方法 ──
    def execute(self, sql, params=None):
        """执行 SQL 并返回 AutoCloseCursor。"""
        # 获取原始 pymysql 连接来创建 cursor
        raw = self._conn._con if hasattr(self._conn, "_con") else self._conn
        cur = raw.cursor()
        try:
            cur.execute(sql, params)
        except Exception:
            try:
                cur.close()
            except Exception as _e:
                _log.debug("execute + execute + execute: %s", _e, exc_info=True)
            raise
        return _AutoCloseCursor(cur)

    def execute_and_close(self, sql, params=None):
        """执行并立即关闭 cursor，适合 INSERT/UPDATE/DELETE。"""
        ac = self.execute(sql, params)
        ac.close()
        return ac

    def executemany(self, sql, params=None):
        """批量执行 SQL（替逐行 execute），返回影响的行数。

        ★ 通用修复(P0-3 3b): 补充 wrapper 缺失的 executemany 便捷方法。
        根因：SteadyDBConnection 无 executemany，wrapper __getattr__ 代理不到。
        覆盖：nav_graph_store / review_lane_store / _modeling_engine / rag_usage_logger 等所有 conn.executemany 调用。
        """
        raw = self._conn._con if hasattr(self._conn, "_con") else self._conn
        cur = raw.cursor()
        try:
            return cur.executemany(sql, params)
        finally:
            try:
                cur.close()
            except Exception as _e:
                _log.debug("[db_wrapper] 操作异常: %s", _e, exc_info=True)

    def fetchone_sql(self, sql, params=None):
        """执行查询并返回第一行。"""
        return self.execute(sql, params).fetchone()

    def fetchall_sql(self, sql, params=None):
        """执行查询并返回所有行。"""
        return self.execute(sql, params).fetchall()

    # ── 事务方法 ──
    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def cursor(self):
        """返回原始连接的标准 cursor。"""
        raw = self._conn._con if hasattr(self._conn, "_con") else self._conn
        return raw.cursor()

    # ── 生命周期 ──
    def close(self):
        """归还连接到池。"""
        from src.core.db_manager._utils import _untrack_connection

        _untrack_connection(self)  # ★ B-004: 连接追踪清理
        self._conn.close()

    def ping(self, reconnect=True):
        return self._conn.ping(reconnect=reconnect)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc[0] is None and exc[1] is None and exc[2] is None:
            self.commit()
        else:
            self.rollback()
        return False


class _AutoCloseCursor:
    """包装 pymysql cursor，在完成数据读取后自动关闭自身。"""

    __slots__ = ("_cur", "_closed")

    def __init__(self, cur):
        object.__setattr__(self, "_cur", cur)
        object.__setattr__(self, "_closed", False)

    def __getattr__(self, item):
        cur = object.__getattribute__(self, "_cur")
        return getattr(cur, item)

    def __setattr__(self, key, value):
        if key in ("_cur", "_closed"):
            object.__setattr__(self, key, value)
            return
        cur = object.__getattribute__(self, "_cur")
        setattr(cur, key, value)

    def fetchone(self):
        cur = object.__getattribute__(self, "_cur")
        try:
            return cur.fetchone()
        finally:
            self.close()

    def fetchall(self):
        cur = object.__getattribute__(self, "_cur")
        try:
            return cur.fetchall()
        finally:
            self.close()

    def fetchmany(self, size=None):
        cur = object.__getattribute__(self, "_cur")
        try:
            if size is None:
                return cur.fetchmany()
            return cur.fetchmany(size)
        finally:
            self.close()

    def close(self):
        if object.__getattribute__(self, "_closed"):
            return
        object.__setattr__(self, "_closed", True)
        cur = object.__getattribute__(self, "_cur")
        try:
            cur.close()
        except Exception as _e:
            _log.debug("close() + close + close(): %s", _e, exc_info=True)

    def __iter__(self):
        cur = object.__getattribute__(self, "_cur")
        return iter(cur)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception as _e:
            _log.debug("__enter__ + __exit__ + close(): %s", _e, exc_info=True)
