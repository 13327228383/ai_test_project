# -*- coding: utf-8 -*-
"""
数据库测试路由处理函数。
─────────────────────
api_db_test
"""

import logging
from flask import jsonify

_log = logging.getLogger(__name__)


def api_db_test():
    """POST /api/db-test — 测试数据库连接（项目级 admin 权限）。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin
    from src.models.permission import has_project_permission
    from src.backend.state import _current_project

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    proj = _current_project()
    if not proj:
        return jsonify({"ok": False, "error": "未选择项目"}), 400
    if not is_admin(uid) and not has_project_permission(
        uid, proj, "datasource.db.test"
    ):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "无权测试数据库连接（项目内需要: datasource.db.test）",
                }
            ),
            403,
        )
    from src.core.config_loader import config

    if not config.db_enabled:
        return jsonify({"ok": False, "error": "未启用数据库"})
    try:
        # ★ 不再用 socket.setdefaulttimeout —— 它是进程级全局副作用，并发请求会互相
        #   污染（A 设 5s 期间 B 的所有 socket 都被改成 5s，含 AI 的 urllib、pymysql 等）。
        #   各驱动已自带 connect_timeout（pymysql/psycopg2=2s）；yasdb 无该参数，
        #   下方用 socket.create_connection 局部预探测（timeout 仅作用于该次连接）。
        db_type_lower = config.db_type.lower()
        if db_type_lower == "mysql":
            import pymysql

            conn = None
            try:
                conn = pymysql.connect(
                    host=config.db_host,
                    port=config.db_port,
                    user=config.db_user,
                    password=config.db_password,
                    database=config.db_name,
                    charset="utf8mb4",
                    connect_timeout=2,
                )
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception as _e:
                        _log.debug("conn.close + close(): %s", _e, exc_info=True)
        elif db_type_lower == "postgresql":
            import psycopg2

            conn = None
            try:
                conn = psycopg2.connect(
                    host=config.db_host,
                    port=config.db_port,
                    user=config.db_user,
                    password=config.db_password,
                    dbname=config.db_name,
                    connect_timeout=2,
                )
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception as _e:
                        _log.debug("conn.close + close(): %s", _e, exc_info=True)
        elif db_type_lower in ("yasdb", "oracle"):
            import yaspy
            import socket as _sock

            # 局部超时预探测 host:port 可达性，避免 yaspy.connect 无超时参数导致挂死。
            # timeout 仅作用于本次 create_connection，不污染进程全局 socket。
            try:
                _probe = _sock.create_connection(
                    (config.db_host, config.db_port), timeout=2
                )
                _probe.close()
            except OSError as _pe:
                return jsonify({"ok": False, "error": f"网络不通: {_pe}"})
            dsn = f"{config.db_host}:{config.db_port}"
            conn = None
            try:
                conn = yaspy.connect(
                    dsn=dsn, user=config.db_user, password=config.db_password
                )
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception as _e:
                        _log.debug("conn.close + close(): %s", _e, exc_info=True)
        elif db_type_lower == "sqlite":
            import sqlite3

            db_path = config.db_file or config.db_name
            conn = None
            try:
                conn = sqlite3.connect(db_path, check_same_thread=False)
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception as _e:
                        _log.debug(
                            "import + conn.close + close(): %s", _e, exc_info=True
                        )
        else:
            if not config.db_type:
                return jsonify(
                    {
                        "ok": False,
                        "error": "数据库未配置 — 请在项目配置中设置 TARGET_DB_TYPE",
                    }
                )
            return jsonify(
                {"ok": False, "error": f"不支持的数据库类型: {config.db_type}"}
            )
        return jsonify(
            {
                "ok": True,
                "message": f"连接成功: {config.db_host}:{config.db_port}/{config.db_name}",
            }
        )
    except ImportError as e:
        return jsonify({"ok": False, "error": f"数据库驱动未安装: {e}"})
    except OSError as e:
        return jsonify({"ok": False, "error": f"网络不通: {e}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:80]})
