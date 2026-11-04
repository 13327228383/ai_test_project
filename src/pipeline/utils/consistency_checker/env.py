# -*- coding: utf-8 -*-
"""
MySQL 环境检查模块
==================
检查 MySQL 数据库连接状态。
"""

from typing import Any

from src.pipeline.utils.consistency_helpers import (
    Issue,
    _make_issue,
)


def check_mysql_environment() -> dict[str, Any]:
    """检查 MySQL 数据库连接状态。"""
    issues: list[Issue] = []
    from src.core.db_manager import health_check

    hc = health_check()
    if not hc.get("ok"):
        issues.append(
            _make_issue(
                "MYSQL_CONNECTION_FAILED",
                "error",
                "MySQL 连接失败: %s" % hc.get("error", "未知错误"),
                ["检查 src/core/config.yaml 中的 storage.mysql 配置"],
            )
        )
    else:
        issues.append(
            _make_issue(
                "MYSQL_CONNECTION_OK",
                "info",
                "MySQL 连接正常 (%s@%s)" % (hc.get("user", "?"), hc.get("host", "?")),
            )
        )
    return {"issues": issues, "ok": hc.get("ok", False)}
