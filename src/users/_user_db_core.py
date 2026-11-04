# -*- coding: utf-8 -*-
"""用户数据库模块 — 共享核心（常量 + _conn）"""
import logging

_log = logging.getLogger(__name__)

ALLOWED_ROLES = ("admin", "project_admin", "project_member")
ACTION_LOG_RETENTION_DAYS = 90
ACTION_LOG_MAX_ROWS = 100000

PROJECT_LEVEL_HIERARCHY = {"owner": 3, "editor": 2, "viewer": 1}


def _conn():
    from src.core.db_manager import get_connection

    return get_connection()
