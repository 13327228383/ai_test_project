# -*- coding: utf-8 -*-
from __future__ import annotations

"""登录入口 + API Key 查询 + 登录追踪 — 从 auth.py 拆出，零业务逻辑变更。"""

import logging
from datetime import datetime

from .password import _verify_password
from .rate_limit import check_login_rate, record_login_success, record_login_failure

_log = logging.getLogger(__name__)


def has_api_key(user_id: str) -> bool:
    """检查用户是否在 user_credentials_v2 中配置了 API Key。"""
    if not user_id:
        return False
    from src.users.user_db import _conn

    c = None
    try:
        c = _conn()
        row = c.execute(
            "SELECT 1 FROM user_credentials_v2 WHERE user_id=%s AND `key`='API_KEY' AND value!=''",
            (user_id,),
        ).fetchone()
        return row is not None
    finally:
        if c:
            c.close()


def get_user_api_key(user_id: str, provider: str = "") -> str:
    """
    从 user_credentials_v2 获取用户 API Key。
    provider 参数已废弃，保留仅用于接口兼容。
    """
    from src.users.user_db import _conn

    c = None
    try:
        c = _conn()
        row = c.execute(
            "SELECT value FROM user_credentials_v2 "
            "WHERE user_id=%s AND `key`='API_KEY' AND project='*'",
            (user_id,),
        ).fetchone()
        return row["value"] if row else ""
    finally:
        if c:
            c.close()


def login(
    user_id: str,
    password: str,
    ip_address: str = "",
    user_agent: str = "",
    skip_rate_check: bool = False,
) -> dict | None:
    """
    验证登录（含账户锁定检查）。
    成功返回用户字典，失败返回 None。
    锁定策略：连续5次失败锁定15分钟（MySQL 持久化）。

    skip_rate_check=True：调用方已做频率检查，跳过重复查询（减少 1 次 MySQL 往返）。
    """
    # ★ 账户锁定检查（调用方可提前检查以跳过重复查询）
    if not skip_rate_check:
        ok_rate, err_rate = check_login_rate(user_id)
        if not ok_rate:
            _record_login_tracking(user_id, False, ip_address, err_rate, user_agent)
            _log.info(f"[Manager] 登录被拒(账户锁定): {user_id} - {err_rate}")
            return None

    from src.users.user_db import _conn

    c = None
    try:
        c = _conn()
        row = c.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()

        if row and _verify_password(password, row["password"]):
            # 登录成功：重置失败计数，更新 last_login
            record_login_success(user_id)
            _record_login_tracking(user_id, True, ip_address, "", user_agent)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            c.execute("UPDATE users SET last_login=%s WHERE id=%s", (now, user_id))
            c.commit()
            return dict(row)
        else:
            # 登录失败：记录失败次数（可能触发锁定）
            record_login_failure(user_id)
            _record_login_tracking(user_id, False, ip_address, "密码错误", user_agent)
            return None
    finally:
        if c:
            c.close()


def _record_login_tracking(
    user_id: str,
    success: bool,
    ip_address: str = "",
    detail: str = "",
    user_agent: str = "",
):
    """记录登录尝试到 login_tracking 表。

    ★ login_tracking.user_agent 是 NOT NULL 无默认值，必须显式写入，否则 INSERT 报错。
    """
    c = None
    try:
        from src.users.user_db import _conn

        c = _conn()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "INSERT INTO login_tracking(user_id, action, ip_address, user_agent, detail, created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (
                user_id,
                "login" if success else "login_failed",
                ip_address,
                user_agent or "",
                detail,
                now,
            ),
        )
        c.commit()
    except Exception as _e:
        _log.debug("[login] 登录追踪失败(不影响主流程): %s", _e, exc_info=True)
    finally:
        if c:
            c.close()
