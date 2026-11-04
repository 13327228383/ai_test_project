# -*- coding: utf-8 -*-
"""
Schema 操作 Mixin
=================
包含表存在性检查、FLASHBACK 回滚等 Schema 相关方法。
"""

from typing import Any


class DbAdapterSchemaMixin:
    """提供 Schema 操作与数据库特定辅助方法。"""

    # =========================================================================
    # 表存在性检查（按数据库类型分发）
    # =========================================================================

    def table_exists(self, table: str) -> bool:
        """检查表是否存在（按数据库类型分发）。
        ★ 使用 _sanitize_ident 净化表名，防止 SQL 注入。"""
        self._ensure_connected()
        db = (self._db_type or "").lower()
        tbl = self._sanitize_ident(table).upper()
        if not tbl:
            return False
        qt = self._quote_ident(tbl)
        if db == "yasdb":
            result = self.query(
                f"SELECT COUNT(*) AS cnt FROM USER_TABLES WHERE TABLE_NAME = {qt}"
            )
        elif db == "oracle":
            result = self.query(
                f"SELECT COUNT(*) AS cnt FROM USER_TABLES WHERE TABLE_NAME = {qt}"
            )
        elif db == "mysql":
            qdb = self._quote_ident(self._database or "")
            result = self.query(
                f"SELECT COUNT(*) AS cnt FROM information_schema.TABLES "
                f"WHERE TABLE_SCHEMA = {qdb} AND TABLE_NAME = {qt}"
            )
        elif db == "postgresql":
            result = self.query(
                f"SELECT COUNT(*) AS cnt FROM information_schema.tables "
                f"WHERE table_schema = 'public' AND table_name = LOWER({qt})"
            )
        elif db == "sqlite":
            result = self.query(
                f"SELECT COUNT(*) AS cnt FROM sqlite_master "
                f"WHERE type = 'table' AND name = {qt}"
            )
        else:
            return False
        return result["success"] and result["data"] and result["data"][0]["cnt"] > 0

    # =========================================================================
    # 崖山 FLASHBACK 回滚支持
    # =========================================================================

    def flashback_table(self, table: str, to_timestamp: str) -> dict[str, Any]:
        """
        将指定表回滚到指定时间点（崖山 FLASHBACK TABLE）。

        参数:
            table:        表名
            to_timestamp: 目标时间点，格式 "YYYY-MM-DD HH:MM:SS"

        返回:
            {"success": True/False, "error": "..."}
        """
        db = (self._db_type or "").lower()
        if db not in ("yasdb", "oracle"):
            return {
                "success": False,
                "error": "FLASHBACK 仅支持崖山/Oracle，当前: %s" % db,
            }

        self._ensure_connected()
        sql = (
            "FLASHBACK TABLE \"%s\" TO TIMESTAMP TO_TIMESTAMP('%s', 'YYYY-MM-DD HH24:MI:SS')"
            % (
                table.upper(),
                to_timestamp,
            )
        )
        try:
            self._cursor.execute(sql)
            self._conn.commit()
            return {"success": True, "table": table, "to_timestamp": to_timestamp}
        except Exception as e:
            return {"success": False, "error": str(e), "sql": sql}
