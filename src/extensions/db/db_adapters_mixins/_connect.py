# -*- coding: utf-8 -*-
"""
连接 Mixin
==========
包含所有 DB 类型特定的连接方法。
"""

import logging
from src.core.guarded import guard

_log = logging.getLogger(__name__)


class DbAdapterConnectMixin:
    """提供跨数据库类型的连接能力。"""

    # =========================================================================
    # 数据库连接（多驱动分发）
    # =========================================================================

    def connect(self):
        """建立数据库连接（多驱动分发），返回 self 以支持链式调用。
        已连接时跳过，避免重复创建连接导致资源泄漏。"""
        if self._conn is not None:
            return self

        # ★ Fix F(2026-09-27)：建立连接前检测 DB 配置漂移并重解析。
        #   根因：DatabaseAgent 按线程缓存实例，_resolve_config 只在构造时跑一次，
        #   项目切换后仍持有旧库配置（连默认 mysql 无库名）→ (1046, 'No database selected')。
        with guard("connect: self.refresh_config_if_stale()", log=_log):
            self.refresh_config_if_stale()

        # ★ Fix: 默认 db_type 改为 mysql（AGENTS.md §3 红线: MySQL 是唯一结构化事实源）
        #   原默认 "yasdb" 会触发 yaspy import，与"MySQL 唯一事实源"冲突
        db = (self._db_type or "mysql").lower()
        dsn = f"{self._host}:{self._port}"

        if db == "yasdb":
            try:
                import yaspy
            except ImportError:
                raise ImportError("yaspy 未安装，执行: pip install yaspy-xxx.whl")
            self._conn = yaspy.connect(
                dsn=dsn, user=self._user, password=self._password
            )
            self._cursor = self._conn.cursor()

        elif db == "mysql":
            try:
                import pymysql
            except ImportError:
                raise ImportError("pymysql 未安装，执行: pip install pymysql")
            self._conn = pymysql.connect(
                host=self._host,
                port=self._port,
                user=self._user,
                password=self._password,
                database=self._database,
                charset="utf8mb4",
                autocommit=self._auto_commit,
                connect_timeout=10,
                read_timeout=30,
                write_timeout=30,
            )
            self._cursor = self._conn.cursor()

        elif db == "postgresql":
            try:
                import psycopg2
            except ImportError:
                raise ImportError("psycopg2 未安装，执行: pip install psycopg2")
            self._conn = psycopg2.connect(
                host=self._host,
                port=self._port,
                user=self._user,
                password=self._password,
                database=self._database,
            )
            self._conn.autocommit = self._auto_commit
            self._cursor = self._conn.cursor()

        elif db == "oracle":
            try:
                import cx_Oracle
            except ImportError:
                raise ImportError("cx_Oracle 未安装，执行: pip install cx_Oracle")
            dsn_tns = cx_Oracle.makedsn(
                self._host, self._port, service_name=self._database
            )
            self._conn = cx_Oracle.connect(
                user=self._user,
                password=self._password,
                dsn=dsn_tns,
            )
            self._cursor = self._conn.cursor()

        elif db == "sqlite":
            import sqlite3

            # ★ 2026-09-23：路径缺失时给出可诊断的显式错误，替代 sqlite3 的
            #   "expected str, bytes or os.PathLike object, not NoneType"（同 db_core/execute.py 口径）。
            if not str(self._database or "").strip():
                raise ValueError(
                    "SQLite 文件路径未配置 — 请在项目配置中设置 TARGET_DB_FILE"
                )
            self._conn = sqlite3.connect(self._database, check_same_thread=False)
            self._cursor = self._conn.cursor()

        else:
            if not self._db_type:
                raise ValueError(
                    "数据库未配置 — 请在项目配置中设置 TARGET_DB_TYPE（支持: yasdb / mysql / postgresql / oracle / sqlite）"
                )
            raise ValueError(
                f"不支持的数据库类型: {self._db_type} (支持: yasdb / mysql / postgresql / oracle / sqlite)"
            )

        _log.info(f"[DatabaseAgent] 已连接 ({db})")
        return self
