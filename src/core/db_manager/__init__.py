# -*- coding: utf-8 -*-
"""
统一 MySQL 连接管理器（PooledDB 版）
=====================================
使用 DBUtils.PooledDB 管理连接池，替代旧的手写线程字典池。

核心特性：
  - conn.close() 自动归还连接到池（非真正关闭 TCP）
  - 池满时阻塞等待（blocking=True），不抛异常
  - 借用时自动 ping 检测存活（ping=1）

使用方式：
    from src.core.db_manager import get_connection
    conn = get_connection()
    cur = conn.execute("SELECT * FROM users WHERE id=%s", (uid,))
    row = cur.fetchone()
    _log.info(row["name"])  # DictCursor → dict
    conn.close()  # ★ 归还到池（必须！）
"""

import time
import logging

import pymysql

from src.core.db_manager._pool import (
    _init_pool,
    _MAX_RETRIES,
    _RETRY_BACKOFF,
    close_all,
)
from src.core.db_config import DBConfigError
from src.core.db_manager._wrapper import _PooledConnectionWrapper
from src.core.db_manager._utils import (
    _track_connection,
    _untrack_connection,
    release_connection,
    release_thread_connections,
    init_schema_on_startup,
    health_check,
)
from src.core.db_schema import _init_schema, _schema_done

_log = logging.getLogger(__name__)


def get_connection() -> pymysql.Connection:
    """从连接池借用一条 MySQL 连接（自动初始化表结构 + 重试）。

    ★ 调用方必须在用完后调用 conn.close() 归还连接到池！
    ★ Flask 路由若忘记 close，teardown_request 会兜底释放。

    ★ B3 fail-fast(2026-08-05) [已复核-DeepSeek-V4-Flash 2026-08-05]：配置缺失（DBConfigError）**不重试**，立即上抛。
      重试只对"池满 / 网络抖动 / MySQL 短暂不可达"有意义；配置错时重试 5 次
      只会把清晰报错拖成 15 秒退避后的模糊超时。

    抛出:
        DBConfigError: MySQL 配置缺失或非法（立即失败，不重试）。
        RuntimeError:  重试 _MAX_RETRIES 次后仍无法建连。
    """
    last_error = None

    for attempt in range(_MAX_RETRIES):
        try:
            pool = _init_pool()
            raw_conn = pool.connection()
            conn = _PooledConnectionWrapper(raw_conn)
            if not _schema_done:
                _init_schema(conn)
            _track_connection(conn)
            return conn
        except DBConfigError as e:
            _log.error("[DB] MySQL 配置错误，立即失败（不重试）: %s", e)
            raise
        except Exception as e:
            last_error = e
            _log.warning(
                "[DB] 连接失败 (attempt %d/%d): %s", attempt + 1, _MAX_RETRIES, e
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
            continue

    raise RuntimeError(
        "MySQL 连接失败（已重试 %d 次）: %s" % (_MAX_RETRIES, last_error)
    )


# 向后兼容：保留旧导入位置
__all__ = [
    "get_connection",
    "release_connection",
    "release_thread_connections",
    "init_schema_on_startup",
    "health_check",
    "close_all",
]
