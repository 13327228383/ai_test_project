# -*- coding: utf-8 -*-
from __future__ import annotations

"""连接池管理（PooledDB 惰性初始化 + 线程安全）。"""

import threading
import logging

import pymysql
from pymysql.cursors import DictCursor
from dbutils.pooled_db import PooledDB

from src.core.db_config import _load_config

_log = logging.getLogger(__name__)

_MAX_POOL = 60  # ★ 连接池上限

# ★ DB 连接可靠性：超时 + 重试
_CONNECT_TIMEOUT = 10  # pymysql 连接超时（秒）
_READ_TIMEOUT = 30  # pymysql 读取超时（秒）
_MAX_RETRIES = 5  # 连接池满时最多重试 5 次
_RETRY_BACKOFF = 1.0  # 重试退避基数（秒），第 N 次重试等待 N × 1.0 秒
_POOL_ACQUIRE_TIMEOUT = 30  # 获取连接总超时（秒）

# ★ DBUtils PooledDB 连接池实例（惰性初始化）
_pool: PooledDB | None = None
_pool_lock = threading.Lock()


def _open() -> pymysql.Connection:
    """创建并配置一个新的 pymysql 连接（供 PooledDB creator 使用）。

    注意：自定义的 execute/fetchone_sql 等方法由 _PooledConnectionWrapper 提供，
    不再直接注入原始连接。"""
    c = _load_config()
    raw = pymysql.connect(
        host=c["host"],
        port=c["port"],
        user=c["user"],
        password=c["password"],
        database=c["database"],
        charset=c["charset"],
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=_CONNECT_TIMEOUT,
        read_timeout=_READ_TIMEOUT,
    )
    # ★ 设置 sql_mode（autocommit=False 下会产生隐式事务，需 commit 清理）
    cur = raw.cursor()
    cur.execute(
        "SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO'"
    )
    cur.close()
    raw.commit()  # ★ 清理 SET SESSION 产生的隐式事务，避免后续 DDL 卡顿
    return raw


def _init_pool() -> PooledDB:
    """惰性初始化 PooledDB 连接池（线程安全）。"""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        _pool = PooledDB(
            creator=_open,
            mincached=5,  # ★ 启动时预创建连接数
            maxcached=20,  # ★ 最大空闲连接数
            maxshared=0,  # 专用连接模式（不共享）
            maxconnections=_MAX_POOL,  # 最大 60 个连接
            blocking=False,  # 池满时立即抛错（而非无限阻塞），由上层重试
            maxusage=None,  # 不限复用次数
            setsession=None,
            reset=True,  # 归还时 rollback 清理事务
            failures=None,  # 使用默认异常类
            ping=1,  # 借用时 ping 检测存活
        )
        _log.info("[DB] PooledDB 连接池已初始化: max=%d", _MAX_POOL)
        return _pool


def close_all() -> None:
    """应用退出时关闭所有连接。"""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception as _e:
            _log.debug("close_all + close(): %s", _e, exc_info=True)
        _pool = None
    from src.core.db_schema import reset_schema_done

    reset_schema_done()
