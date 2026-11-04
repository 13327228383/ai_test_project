# -*- coding: utf-8 -*-
"""登录锁定机制（MySQL 持久化）— 连续5次失败锁定15分钟。

需求规格说明书 §3.7: 连续5次登录失败锁定账户15分钟，成功后归零。
"""

import logging
from datetime import datetime, timedelta

_log = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = 5
LOCK_DURATION_MINUTES = 15


def check_login_rate(
    user_id: str, max_attempts: int = MAX_FAILED_ATTEMPTS, window_seconds: int = 60
) -> tuple:
    """
    检查账户是否被锁定。返回 (ok: bool, error_msg: str)。

    读取 users.failed_attempts 和 users.locked_until 判断：
    - 若 locked_until > NOW()，返回 (False, "账户已锁定，请X分钟后重试")
    - 否则返回 (True, "")
    """
    if not user_id:
        return True, ""
    from src.users.user_db import _conn

    c = None
    try:
        c = _conn()
        row = c.execute(
            "SELECT failed_attempts, locked_until FROM users WHERE id=%s", (user_id,)
        ).fetchone()
        if not row:
            return True, ""  # 用户不存在，交给后续密码验证处理
        locked_until = row["locked_until"]
        if locked_until:
            now = datetime.now()
            if isinstance(locked_until, str):
                locked_until = datetime.strptime(locked_until, "%Y-%m-%d %H:%M:%S")
            if locked_until > now:
                remain = int((locked_until - now).total_seconds() / 60) + 1
                return False, f"账户已锁定，请 {remain} 分钟后重试"
            # 锁定已过期，自动解锁
            c.execute(
                "UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=%s",
                (user_id,),
            )
            c.commit()
        return True, ""
    except Exception as e:
        _log.warning("[rate_limit] check_login_rate 异常: %s", e)
        return True, ""  # DB异常时放行，交给密码验证兜底
    finally:
        if c:
            c.close()


def record_login_success(user_id: str):
    """登录成功后重置失败计数和解锁。"""
    if not user_id:
        return
    from src.users.user_db import _conn

    c = None
    try:
        c = _conn()
        c.execute(
            "UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=%s",
            (user_id,),
        )
        c.commit()
    except Exception as e:
        _log.warning("[rate_limit] record_login_success 异常: %s", e)
    finally:
        if c:
            c.close()


def record_login_failure(user_id: str):
    """记录登录失败，达到阈值则锁定账户15分钟。"""
    if not user_id:
        return
    from src.users.user_db import _conn

    c = None
    try:
        c = _conn()
        row = c.execute(
            "SELECT failed_attempts FROM users WHERE id=%s", (user_id,)
        ).fetchone()
        if not row:
            return
        new_count = (row["failed_attempts"] or 0) + 1
        if new_count >= MAX_FAILED_ATTEMPTS:
            lock_until = datetime.now() + timedelta(minutes=LOCK_DURATION_MINUTES)
            lock_str = lock_until.strftime("%Y-%m-%d %H:%M:%S")
            c.execute(
                "UPDATE users SET failed_attempts=%s, locked_until=%s WHERE id=%s",
                (new_count, lock_str, user_id),
            )
            c.commit()
            _log.warning(
                "[rate_limit] 用户 %s 连续失败 %d 次，已锁定至 %s",
                user_id,
                new_count,
                lock_str,
            )
        else:
            c.execute(
                "UPDATE users SET failed_attempts=%s WHERE id=%s", (new_count, user_id)
            )
            c.commit()
            _log.info(
                "[rate_limit] 用户 %s 登录失败 %d/%d",
                user_id,
                new_count,
                MAX_FAILED_ATTEMPTS,
            )
    except Exception as e:
        _log.warning("[rate_limit] record_login_failure 异常: %s", e)
    finally:
        if c:
            c.close()
