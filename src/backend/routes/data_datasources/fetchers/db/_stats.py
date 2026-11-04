# -*- coding: utf-8 -*-
"""
数据库统计路由处理函数。
─────────────────────
api_db_stats
"""

import time
import logging
from flask import jsonify

_log = logging.getLogger(__name__)

# ★ 同类错误降噪间隔（秒）— 由 facade 传入，此处为默认值
_DB_STATS_ERROR_COOLDOWN = 60
_db_stats_last_error_ts = 0
_db_stats_last_error_msg = ""


def api_db_stats(_check_login_and_project, _DB_STATS_ERROR_COOLDOWN):
    """GET /api/db-stats — 返回数据库连接与统计信息。"""
    from src.core.config_loader import config

    global _db_stats_last_error_ts, _db_stats_last_error_msg
    ok, err = _check_login_and_project()
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    try:
        target_db_type = str(config.db_type or "").strip()
        target_db_host = str(config.db_host or "").strip()
        target_db_port = config.db_port
        target_db_name = str(config.db_name or "").strip()
        target_db_file = str(config.db_file or "").strip()
        target_enabled = config.db_enabled
        if not target_enabled:
            return jsonify({"connected": False, "message": "未启用数据库"})
        # ★ 检查是否已配置数据库
        if not target_db_type or (
            target_db_type.lower() != "sqlite" and not target_db_host
        ):
            return jsonify(
                {
                    "connected": False,
                    "not_configured": True,
                    "message": "数据库未配置 — 请在侧边栏🗄️数据库配置中设置连接信息",
                }
            )
        # ★ 检查用户级凭据是否已配置
        if target_db_type.lower() != "sqlite" and (
            not config.db_user or not config.db_password
        ):
            return jsonify(
                {
                    "connected": False,
                    "need_config": True,
                    "message": "数据库用户名/密码未配置 — 请点击侧边栏🗄️数据库配置",
                }
            )
        # ★ 失败缓存：60 秒内连接失败过，直接返回缓存错误
        now = time.time()
        if (
            _db_stats_last_error_msg
            and (now - _db_stats_last_error_ts) < _DB_STATS_ERROR_COOLDOWN
        ):
            return jsonify(
                {
                    "connected": False,
                    "error": _db_stats_last_error_msg,
                    "cached": True,
                }
            )
        try:
            db_type_lower = target_db_type.lower()
            if db_type_lower == "mysql":
                try:
                    import pymysql
                except ImportError:
                    return jsonify(
                        {
                            "connected": False,
                            "error": "pymysql 未安装，执行: pip install pymysql",
                        }
                    )
                conn = None
                cursor = None
                try:
                    conn = pymysql.connect(
                        host=target_db_host,
                        port=int(target_db_port),
                        user=config.db_user,
                        password=config.db_password,
                        database=target_db_name,
                        charset="utf8mb4",
                        connect_timeout=2,
                    )
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s",
                        (target_db_name,),
                    )
                    table_count = cursor.fetchone()[0]
                finally:
                    if cursor:
                        try:
                            cursor.close()
                        except Exception as _e:
                            _log.debug(
                                "execute + format + fetchone: %s", _e, exc_info=True
                            )
                    if conn:
                        try:
                            conn.close()
                        except Exception as _e:
                            _log.debug(
                                "fetchone + close() + conn.close: %s", _e, exc_info=True
                            )
            elif db_type_lower == "postgresql":
                try:
                    import psycopg2
                except ImportError:
                    return jsonify(
                        {
                            "connected": False,
                            "error": "psycopg2 未安装，执行: pip install psycopg2",
                        }
                    )
                conn = None
                cursor = None
                try:
                    conn = psycopg2.connect(
                        host=target_db_host,
                        port=int(target_db_port),
                        user=config.db_user,
                        password=config.db_password,
                        dbname=target_db_name,
                        connect_timeout=2,
                    )
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s",
                        (target_db_name,),
                    )
                    table_count = cursor.fetchone()[0]
                finally:
                    if cursor:
                        try:
                            cursor.close()
                        except Exception as _e:
                            _log.debug(
                                "execute + format + fetchone: %s", _e, exc_info=True
                            )
                    if conn:
                        try:
                            conn.close()
                        except Exception as _e:
                            _log.debug(
                                "fetchone + close() + conn.close: %s", _e, exc_info=True
                            )
            elif db_type_lower in ("yasdb", "oracle"):
                try:
                    import yaspy
                except ImportError:
                    return jsonify(
                        {
                            "connected": False,
                            "error": "yaspy 未安装，执行: pip install yaspy",
                        }
                    )
                dsn = f"{target_db_host}:{target_db_port}"
                conn = None
                cursor = None
                try:
                    conn = yaspy.connect(
                        dsn=dsn, user=config.db_user, password=config.db_password
                    )
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM USER_TABLES")
                    table_count = cursor.fetchone()[0]
                finally:
                    if cursor:
                        try:
                            cursor.close()
                        except Exception as _e:
                            _log.debug(
                                "execute + fetchone + close(): %s", _e, exc_info=True
                            )
                    if conn:
                        try:
                            conn.close()
                        except Exception as _e:
                            _log.debug(
                                "fetchone + close() + conn.close: %s", _e, exc_info=True
                            )
            elif db_type_lower == "sqlite":
                import sqlite3

                db_path = target_db_file or target_db_name
                conn = None
                cursor = None
                try:
                    conn = sqlite3.connect(db_path, check_same_thread=False)
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                    )
                    table_count = cursor.fetchone()[0]
                finally:
                    if cursor:
                        try:
                            cursor.close()
                        except Exception as _e:
                            _log.debug(
                                "execute + fetchone + close(): %s", _e, exc_info=True
                            )
                    if conn:
                        try:
                            conn.close()
                        except Exception as _e:
                            _log.debug(
                                "fetchone + close() + conn.close: %s", _e, exc_info=True
                            )
            else:
                return jsonify(
                    {
                        "connected": False,
                        "error": f"不支持的数据库类型: {target_db_type}",
                    }
                )
            # ★ 连接成功后清除错误缓存
            _db_stats_last_error_ts = 0
            _db_stats_last_error_msg = ""
            return jsonify(
                {
                    "connected": True,
                    "db_name": (
                        target_db_file if db_type_lower == "sqlite" else target_db_name
                    ),
                    "db_host": (
                        target_db_host if db_type_lower != "sqlite" else "local-file"
                    ),
                    "db_type": target_db_type,
                    "scope": "target_system",
                    "table_count": table_count,
                }
            )
        except Exception as e:
            err_str = str(e)
            is_auth_err = any(
                kw in err_str.lower()
                for kw in (
                    "invalid username/password",
                    "login denied",
                    "authentication failed",
                    "access denied",
                    "password",
                    "401",
                    "403",
                    "login",
                )
            )
            now = time.time()
            if (
                err_str != _db_stats_last_error_msg
                or (now - _db_stats_last_error_ts) > _DB_STATS_ERROR_COOLDOWN
            ):
                _db_stats_last_error_ts = now
                _db_stats_last_error_msg = err_str
                level = "INFO" if is_auth_err else "ERROR"
                if level == "ERROR":
                    _log.error("[db-stats] %s", e)
                else:
                    _log.info("[db-stats] 认证失败（提示用户配置）")
            if is_auth_err:
                return jsonify(
                    {
                        "connected": False,
                        "need_config": True,
                        "message": "数据库认证失败 — 请检查侧边栏🗄️数据库配置中的用户名和密码",
                    }
                )
            return jsonify(
                {
                    "connected": False,
                    "error": "数据库连接失败 — 请检查数据库配置（主机/端口/库名是否正确）",
                }
            )
    except Exception as e:
        _log.error(f"[ERROR] db-stats: {e}")
        return jsonify({"connected": False, "error": "服务器内部错误"})
