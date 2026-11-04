# -*- coding: utf-8 -*-
"""
DbCoreValidateMixin: _validate_sql — SQL 安全校验
"""

import logging

_log = logging.getLogger(__name__)


class _DbCoreValidateMixin:
    """SQL 安全校验方法。"""

    def _validate_sql(self, sql: str) -> bool:
        """
        SQL 安全校验：拦截危险的语句类型。

        参数：
            sql: 待执行的 SQL 字符串

        返回：
            校验通过 True，拦截 False
        """
        sql_upper = sql.upper().strip()

        # 禁止名单
        forbidden = [
            ("DROP DATABASE", "禁止 DROP DATABASE"),
            ("DROP SCHEMA", "禁止 DROP SCHEMA"),
            ("TRUNCATE", "禁止 TRUNCATE（请使用 DELETE FROM）"),
            ("ALTER TABLE", "禁止 ALTER TABLE（仅允许 DML 操作）"),
            ("CREATE TABLE", "禁止 CREATE TABLE（仅允许 DML 操作）"),
            ("RENAME TABLE", "禁止 RENAME TABLE"),
            ("GRANT", "禁止权限操作"),
            ("REVOKE", "禁止权限操作"),
        ]

        for keyword, reason in forbidden:
            if keyword in sql_upper:
                _log.info("[DbExecutor] 安全拦截: %s", reason)
                return False

        # ★ 对 DELETE / UPDATE 检查是否有 WHERE 子句 — 无条件操作直接拒绝
        for stmt in ["DELETE", "UPDATE"]:
            if stmt in sql_upper:
                if "WHERE" not in sql_upper:
                    _log.info(
                        "[DbExecutor] 安全拦截: %s 语句未包含 WHERE 条件（全表操作被拒绝）",
                        stmt,
                    )
                    return False

        return True
