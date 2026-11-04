# -*- coding: utf-8 -*-
from __future__ import annotations

"""

执行 Mixin

==========

包含 SQL 执行、查询、参数化支持、格式化及辅助方法。

"""


from typing import Any
import re


import logging

_log = logging.getLogger(__name__)

# 危险字符模式：空字节、孤立反斜杠、Unicode 控制字符（模块级，避免每次 SQL 执行重新编译）
_DANGEROUS_RE = re.compile(r"[\x00\x08\x1a\x1b]|\\\\[^\\]")


class DbAdapterExecuteMixin:
    """提供 SQL 执行与查询能力。"""

    # =========================================================================

    # SQL 执行 & 查询

    # =========================================================================

    def execute(self, sql: str, params: tuple | None = None) -> dict[str, Any]:
        """

        执行任意 SQL（INSERT / UPDATE / DELETE）。

        MySQL/PostgreSQL/SQLite 使用驱动原生参数化；崖山/Oracle 手动格式化。



        返回：

            {"success": True, "rows_affected": N, "lastrowid": N}

        """

        self._ensure_connected()

        if self._supports_native_params() and params:

            final_sql = sql

            exec_params = params

        else:

            final_sql = self._format_sql(sql, params)

            exec_params = None

        try:

            if exec_params:

                self._cursor.execute(final_sql, exec_params)

            else:

                self._cursor.execute(final_sql)

            self._conn.commit()

            rowcount = self._cursor.rowcount

            # 驱动级 lastrowid（MySQL/SQLite/PostgreSQL 支持），否则 SELECT MAX(ID) 回退

            lastrowid = (
                self._cursor.lastrowid
                if hasattr(self._cursor, "lastrowid") and self._cursor.lastrowid
                else self._get_last_insert_id(final_sql)
            )

            return {
                "success": True,
                "rows_affected": rowcount,
                "lastrowid": lastrowid,
            }

        except Exception as e:
            try:
                self._conn.rollback()
            except Exception as _e:
                _log.debug("execute rollback failed: %s", _e, exc_info=True)
            return {"success": False, "error": str(e), "sql": final_sql}

    def query(self, sql: str, params: tuple | None = None) -> dict[str, Any]:
        """

        执行 SELECT 查询。

        MySQL/PostgreSQL/SQLite 使用驱动原生参数化；崖山/Oracle 手动格式化。



        返回：

            {"success": True, "data": [row_dict, ...], "row_count": N}

        """

        self._ensure_connected()

        if self._supports_native_params() and params:

            final_sql = sql

            exec_params = params

        else:

            final_sql = self._format_sql(sql, params)

            exec_params = None

        try:

            if exec_params:

                self._cursor.execute(final_sql, exec_params)

            else:

                self._cursor.execute(final_sql)

            rows = self._cursor.fetchall()

            if rows:

                columns = [desc[0] for desc in self._cursor.description]

                data = [dict(zip(columns, row)) for row in rows]

            else:

                data = []

            return {"success": True, "data": data, "row_count": len(data)}

        except Exception as e:

            return {"success": False, "error": str(e), "sql": final_sql}

    # =========================================================================

    # 参数化支持检测

    # =========================================================================

    def _supports_native_params(self) -> bool:
        """MySQL/PostgreSQL/SQLite 等驱动原生支持参数化，崖山/Oracle 不支持。"""

        db = (self._db_type or "").lower()

        return db in ("mysql", "postgresql", "sqlite")

    # =========================================================================

    # SQL 手动格式化（仅崖山/Oracle）

    # =========================================================================

    @staticmethod
    def _format_sql(sql: str, params: tuple | None) -> str:
        """手动将 %s 占位符替换为字面值（仅崖山/Oracle 使用——它们不支持驱动级参数化）。



        ⚠ 安全增强：拒绝包含空字节、反斜杠注入特征的参数值。

        字符串值仅允许常见可打印字符，阻断 Unicode 归一化攻击面。

        """

        if not params:

            return sql

        formatted = sql

        for p in params:

            if p is None:

                formatted = formatted.replace("%s", "NULL", 1)

            elif isinstance(p, (int, float)):

                formatted = formatted.replace("%s", str(p), 1)

            else:

                s = str(p)

                # 安全校验：拒绝含危险字符的值

                if _DANGEROUS_RE.search(s):

                    raise ValueError("SQL 参数包含危险字符，已拒绝: %s" % repr(s[:80]))

                # 标准 SQL 转义：单引号 → 两个单引号

                safe = s.replace("'", "''")

                formatted = formatted.replace("%s", f"'{safe}'", 1)

        return formatted

    # =========================================================================

    # 最后插入 ID 回退（仅崖山）

    # =========================================================================

    def _get_last_insert_id(self, sql: str) -> int:
        """崖山不支持 lastrowid → 解析 INSERT 语句的表名后 SELECT MAX(ID)。

        表名经正则提取后做安全清理，仅保留字母数字下划线。"""

        import re

        match = re.search(
            r'INSERT\s+INTO\s+((?:"?\w+"?\.)?"?\w+"?)', sql, re.IGNORECASE
        )

        if not match:

            return 0

        table = self._sanitize_ident(match.group(1).strip('"'))

        if not table:

            return 0

        try:

            from src.core.db_security import validate_table_name

            if not validate_table_name(table):

                return 0

            self._cursor.execute(f"SELECT MAX(ID) FROM {table}")

            row = self._cursor.fetchone()

            return int(row[0]) if row and row[0] else 0

        except Exception as e:
            _log.warning("SELECT MAX(ID)异常: %s", e, exc_info=True)
            return 0

    # =========================================================================

    # 跨数据库辅助方法（LIMIT 语法 + 标识符引用）

    # =========================================================================

    def _limit_clause(self, n: int) -> str:
        """根据当前数据库类型返回正确的行数限制子句。"""

        db = (self._db_type or "").lower()

        if db in ("yasdb", "oracle"):

            return f"FETCH FIRST {n} ROWS ONLY"

        else:

            return f"LIMIT {n}"

    def _quote_ident(self, name: str) -> str:
        """根据当前数据库类型返回正确引用的标识符。"""

        db = (self._db_type or "").lower()

        if db == "mysql":

            return f"`{name}`"

        else:

            return f'"{name}"'
