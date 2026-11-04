# -*- coding: utf-8 -*-
"""SystemDataResolver — 数据库访问（独立连接，用完即关）。"""

import re
import logging
from typing import Any

_log = logging.getLogger(__name__)


class DbAccessMixin:
    """数据库连接、表/列查询、SQL 安全。"""

    def _connect_db(self):
        try:
            from src.extensions.db.db_agent import DatabaseAgent

            agent = DatabaseAgent()
            agent.connect()
            return agent
        except Exception as e:
            _log.info("[SystemDataResolver] DB 连接失败: %s", e)
            return None

    def _get_all_tables_with_columns(
        self, db_agent, db_type: str
    ) -> dict[str, list[str]]:
        """获取所有非排除表及其列名。"""
        # 获取表列表
        result = self._query_table_list(db_agent, db_type)
        if not result.get("success") or not result.get("data"):
            return {}

        tables = [
            str(name)
            for row in result["data"]
            for name in [list(row.values())[0] if isinstance(row, dict) else row[0]]
            if not self._is_excluded(str(name))
        ]

        # 获取每张表的列
        tables_with_cols = {}
        for t in tables:
            cols = self._query_table_columns(db_agent, db_type, t)
            if cols:
                tables_with_cols[t.upper()] = cols

        return tables_with_cols

    def _query_table_list(self, db_agent, db_type: str) -> dict[str, Any]:
        if db_type in ("yasdb", "oracle"):
            return db_agent.query("SELECT TABLE_NAME FROM USER_TABLES")
        elif db_type == "mysql":
            from src.core.config_loader import config

            # ★ P1: 使用参数化查询，避免 SQL 注入
            return db_agent.query(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s",
                (config.db_name,),
            )
        elif db_type == "postgresql":
            return db_agent.query(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public'"
            )
        elif db_type == "sqlite":
            return db_agent.query("SELECT name FROM sqlite_master WHERE type='table'")
        return {"success": False, "data": []}

    @staticmethod
    def _safe_name(name: str) -> str:
        """净化表名/列名：仅保留字母、数字、下划线，防止SQL注入。"""
        return re.sub(r"[^\w]", "", str(name))

    def _query_table_columns(self, db_agent, db_type: str, table: str) -> list[str]:
        """查询表列名列表（兼容接口，仅返回列名大写列表）。"""
        try:
            tname = self._safe_name(table)
            if db_type in ("yasdb", "oracle"):
                # ★ P1: 使用参数化查询
                r = db_agent.query(
                    "SELECT COLUMN_NAME FROM USER_TAB_COLUMNS "
                    "WHERE TABLE_NAME=%s ORDER BY COLUMN_ID",
                    (tname,),
                )
            elif db_type == "mysql":
                # ★ H2 (2026-07-10): 改用 information_schema.COLUMNS（DESC 只返回列名丢失类型/注释）
                from src.core.config_loader import config

                r = db_agent.query(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
                    "ORDER BY ORDINAL_POSITION",
                    (config.db_name(), tname),
                )
            elif db_type == "postgresql":
                # ★ P1: 使用参数化查询
                r = db_agent.query(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=%s AND table_schema='public' "
                    "ORDER BY ordinal_position",
                    (tname,),
                )
            elif db_type == "sqlite":
                r = db_agent.query("PRAGMA table_info('%s')" % tname)
            else:
                return []
            if r.get("success") and r.get("data"):
                return [list(row.values())[0].upper() for row in r["data"]]
        except Exception as _e:
            _log.debug("未知: %s", _e, exc_info=True)
        return []

    def _is_excluded(self, table: str) -> bool:
        """检查表名是否应排除（日志/审计/历史/临时表）。使用词边界检查防止误匹配。"""
        tl = table.lower()
        for kw in self.EXCLUDE_TABLE_KW:
            idx = tl.find(kw)
            if idx == -1:
                continue
            if kw.startswith("_"):
                end = idx + len(kw)
                if end < len(tl) and tl[end] != "_":
                    continue
            elif idx > 0 and tl[idx - 1] != "_":
                continue
            return True
        return False
