# -*- coding: utf-8 -*-
"""
用户认证模块 — 加密 + 密码 + 登录
从 manager.py 拆分。

【2026-06-18 重构】原 400 行单文件按职责拆分：
- encryption.py — Fernet 加密（_encrypt / _decrypt / _load_or_create_key / MySQL 密钥读写）
- password.py   — 密码强度校验 + SHA-256 哈希（validate_password_strength / _hash / _verify_password）
- rate_limit.py — 登录频率限制（check_login_rate / record_login_success/failure）
- login.py      — 登录入口 + API Key（login / has_api_key / get_user_api_key / _record_login_tracking）
- __init__.py   — require_auth 装饰器 + 全部 re-export
"""

from functools import wraps
from flask import jsonify

# ── re-export 全部子模块符号 ──
from .encryption import (
    _SYS_CONFIG_KEY,
    _fernet_instance,
    _fernet_lock,
    _get_mysql_conn,
    _read_key_from_mysql,
    _write_key_to_mysql,
    _load_or_create_key,
    _encrypt,
    _decrypt,
    _decrypt_auto,
    _encrypt_auto,
)
from .password import (
    validate_password_strength,
    _hash,
    _verify_password,
)
from .rate_limit import (
    check_login_rate,
    record_login_success,
    record_login_failure,
    MAX_FAILED_ATTEMPTS,
    LOCK_DURATION_MINUTES,
)
from .login import (
    has_api_key,
    get_user_api_key,
    login,
    _record_login_tracking,
)


# ═══════════════════════════════════════════════════════════════
# Flask 路由认证装饰器
# ═══════════════════════════════════════════════════════════════


def require_auth(f):
    """
    Flask 路由装饰器：要求当前用户已登录。
    未登录时返回 401 JSON 响应。

    用法：
        from src.users.auth import require_auth

        @synonym_bp.route("/api/synonyms")
        @require_auth
        def api_list_synonyms():
            ...
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        from src.core.config_loader import get_current_user

        uid = get_current_user()
        if not uid:
            return jsonify({"success": False, "error": "请先登录"}), 401
        return f(*args, **kwargs)

    return decorated


__all__ = [
    # encryption
    "_get_mysql_conn",
    "_read_key_from_mysql",
    "_write_key_to_mysql",
    "_load_or_create_key",
    "_encrypt",
    "_decrypt",
    "_decrypt_auto",
    "_encrypt_auto",
    # password
    "validate_password_strength",
    "_hash",
    "_verify_password",
    # rate_limit
    "check_login_rate",
    "record_login_success",
    "record_login_failure",
    # login
    "has_api_key",
    "get_user_api_key",
    "login",
    "_record_login_tracking",
    # decorator
    "require_auth",
]
