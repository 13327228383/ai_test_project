# -*- coding: utf-8 -*-
"""
DbCoreExecuteMixin: _execute_sql + 5 个数据库实现
"""

from typing import Any  # 类型标注
import logging

_log = logging.getLogger(__name__)


class _DbCoreExecuteMixin:
    """SQL 执行方法（调度 + 5 种数据库实现）。"""

    def _execute_sql(self, sql: str) -> dict[str, Any]:
        """
        连接数据库并执行 SQL 语句。

        支持崖山（yaspy）、Oracle（yaspy/cx_Oracle）、MySQL（pymysql）、
        PostgreSQL（psycopg2）、SQLite（sqlite3）。

        参数：
            sql: 完整的 SQL 字符串（可包含多条分号分隔的语句）

        返回：
            {"success": True, "rows_affected": N, "data": [...]}
        """
        import time as _time

        db_type = self._db_type.lower()

        max_retries = 3
        base_delay = 0.5
        last_error = None

        for attempt in range(1, max_retries + 1):
            result = None
            if db_type == "sqlite":
                result = self._execute_sqlite(sql)
            elif db_type == "mysql":
                result = self._execute_mysql(sql)
            elif db_type == "postgresql":
                result = self._execute_postgresql(sql)
            elif db_type == "yasdb":
                result = self._execute_yasdb(sql)
            elif db_type == "oracle":
                result = self._execute_oracle(sql)
            else:
                if not db_type:
                    return {
                        "success": False,
                        "error": "数据库未配置 — 请在项目配置中设置 TARGET_DB_TYPE（支持 yasdb / oracle / mysql / postgresql / sqlite）",
                    }
                return {
                    "success": False,
                    "error": "不支持的数据库类型: %s（支持 yasdb / oracle / mysql / postgresql / sqlite）"
                    % db_type,
                }

            if result.get("success"):
                return result

            last_error = result.get("error", "")
            # 判断是否为可重试错误（连接类、锁冲突、超时）
            retryable = any(
                kw in str(last_error).lower()
                for kw in [
                    "connection",
                    "connect",
                    "timeout",
                    "lock",
                    "deadlock",
                    "busy",
                    "temporarily",
                    "retry",
                    "network",
                    "refused",
                    "reset",
                    "closed",
                ]
            )
            if not retryable or attempt == max_retries:
                return result

            delay = base_delay * (2 ** (attempt - 1))
            _log.warning(
                "[DbExecutor] 第 %d/%d 次执行失败，%.1f 秒后重试: %s",
                attempt,
                max_retries,
                delay,
                last_error,
            )
            _time.sleep(delay)

        return {
            "success": False,
            "error": last_error or "执行失败（已重试 %d 次）" % max_retries,
        }

    def _execute_sqlite(self, sql: str) -> dict[str, Any]:
        """SQLite 执行逻辑。"""
        import sqlite3

        db_path = self._db_file
        if not db_path:
            return {"success": False, "error": "SQLite 文件路径未配置 (TARGET_DB_FILE)"}

        conn = None
        try:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.isolation_level = None  # 关闭自动提交，手动控制事务
            cursor = conn.cursor()
            cursor.execute("BEGIN")

            statements = [x for s in sql.split(";") if (x := s.strip())]
            total_affected = 0
            all_data = []

            for stmt in statements:
                cursor.execute(stmt)
                total_affected += cursor.rowcount

                if stmt.upper().strip().startswith("SELECT"):
                    rows = cursor.fetchall()
                    if rows:
                        columns = [desc[0] for desc in cursor.description]
                        all_data = [dict(zip(columns, row)) for row in rows]

            conn.commit()

            _log.info("[DbExecutor] SQLite 执行成功 — 影响行数: %d", total_affected)
            return {
                "success": True,
                "rows_affected": total_affected,
                "data": all_data,
            }

        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception as _e:
                    _log.debug("未知: %s", _e, exc_info=True)
            return {"success": False, "error": "SQLite 执行失败: %s" % e}
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close + close(): %s", _e, exc_info=True)

    def _execute_mysql(self, sql: str) -> dict[str, Any]:
        """MySQL 执行逻辑（pymysql 驱动）。"""
        try:
            import pymysql
        except ImportError:
            return {
                "success": False,
                "error": "pymysql 未安装，请执行: pip install pymysql",
            }
        conn = None
        try:
            conn = pymysql.connect(
                host=self._db_host,
                port=self._db_port,
                user=self._db_user,
                password=self._db_pass,
                database=self._db_name,
                charset="utf8mb4",
                autocommit=False,
            )
            cursor = conn.cursor()
            statements = [x for s in sql.split(";") if (x := s.strip())]
            total_affected = 0
            all_data = []
            for stmt in statements:
                cursor.execute(stmt)
                total_affected += cursor.rowcount
                if stmt.upper().strip().startswith("SELECT"):
                    rows = cursor.fetchall()
                    if rows:
                        columns = [desc[0] for desc in cursor.description]
                        all_data = [dict(zip(columns, row)) for row in rows]
            conn.commit()
            _log.info("[DbExecutor] MySQL 执行成功 — 影响行数: %d", total_affected)
            return {"success": True, "rows_affected": total_affected, "data": all_data}
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception as _e:
                    _log.debug("commit: %s", _e, exc_info=True)
            return {"success": False, "error": "MySQL 执行失败: %s" % e}
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close + close(): %s", _e, exc_info=True)

    def _execute_yasdb(self, sql: str) -> dict[str, Any]:
        """崖山 / Oracle 数据库执行逻辑（yaspy 驱动，兼容 Oracle）。"""
        try:
            import yaspy
        except ImportError:
            return {"success": False, "error": "yaspy 未安装"}
        conn = None
        try:
            dsn = f"{self._db_host}:{self._db_port}"
            conn = yaspy.connect(dsn=dsn, user=self._db_user, password=self._db_pass)
            cursor = conn.cursor()
            statements = [x for s in sql.split(";") if (x := s.strip())]
            total_affected = 0
            all_data = []
            for stmt in statements:
                cursor.execute(stmt)
                total_affected += cursor.rowcount
                if stmt.upper().strip().startswith("SELECT"):
                    rows = cursor.fetchall()
                    if rows:
                        columns = [desc[0] for desc in cursor.description]
                        all_data = [dict(zip(columns, row)) for row in rows]
            conn.commit()
            _log.info("[DbExecutor] 崖山执行成功 — 影响行数: %d", total_affected)
            return {"success": True, "rows_affected": total_affected, "data": all_data}
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception as _e:
                    _log.debug("commit: %s", _e, exc_info=True)
            return {"success": False, "error": "崖山执行失败: %s" % e}
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close + close(): %s", _e, exc_info=True)

    def _execute_oracle(self, sql: str) -> dict[str, Any]:
        """Oracle 数据库执行逻辑（cx_Oracle 驱动）。"""
        try:
            import cx_Oracle
        except ImportError:
            return {
                "success": False,
                "error": "cx_Oracle 未安装，请执行: pip install cx_Oracle",
            }
        conn = None
        try:
            dsn = cx_Oracle.makedsn(
                self._db_host, self._db_port, service_name=self._db_name
            )
            conn = cx_Oracle.connect(
                user=self._db_user, password=self._db_pass, dsn=dsn
            )
            cursor = conn.cursor()
            statements = [x for s in sql.split(";") if (x := s.strip())]
            total_affected = 0
            all_data = []
            for stmt in statements:
                cursor.execute(stmt)
                total_affected += cursor.rowcount
                if stmt.upper().strip().startswith("SELECT"):
                    rows = cursor.fetchall()
                    if rows:
                        columns = [desc[0] for desc in cursor.description]
                        all_data = [dict(zip(columns, row)) for row in rows]
            conn.commit()
            _log.info("[DbExecutor] Oracle 执行成功 — 影响行数: %d", total_affected)
            return {"success": True, "rows_affected": total_affected, "data": all_data}
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception as _e:
                    _log.debug("commit: %s", _e, exc_info=True)
            return {"success": False, "error": "Oracle 执行失败: %s" % e}
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close + close(): %s", _e, exc_info=True)

    def _execute_postgresql(self, sql: str) -> dict[str, Any]:
        """PostgreSQL 执行逻辑（需要 psycopg2）。"""
        try:
            import psycopg2
        except ImportError:
            return {
                "success": False,
                "error": "psycopg2 未安装，请执行: pip install psycopg2",
            }

        conn = None
        try:
            conn = psycopg2.connect(
                host=self._db_host,
                port=self._db_port,
                user=self._db_user,
                password=self._db_pass,
                dbname=self._db_name,
            )
            conn.autocommit = False
            cursor = conn.cursor()

            statements = [x for s in sql.split(";") if (x := s.strip())]
            total_affected = 0
            all_data = []

            for stmt in statements:
                cursor.execute(stmt)
                total_affected += cursor.rowcount

                if stmt.upper().strip().startswith("SELECT"):
                    rows = cursor.fetchall()
                    if rows:
                        columns = [desc[0] for desc in cursor.description]
                        all_data = [dict(zip(columns, row)) for row in rows]

            conn.commit()

            _log.info("[DbExecutor] PostgreSQL 执行成功 — 影响行数: %d", total_affected)
            return {
                "success": True,
                "rows_affected": total_affected,
                "data": all_data,
            }

        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception as _e:
                    _log.debug("未知: %s", _e, exc_info=True)
            return {"success": False, "error": "PostgreSQL 执行失败: %s" % e}
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close + close(): %s", _e, exc_info=True)
