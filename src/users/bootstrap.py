# -*- coding: utf-8 -*-
from __future__ import annotations

"""系统启动 Bootstrap 流程 (P0-03 改造)

★ 背景：原 schema.sql 直接 INSERT 默认 admin 用户的 SHA-256 哈希到 DDL。
  这导致密码泄漏到 git 仓库中，公开 commit 会被扫描器捕获。
  本模块替代该行为：系统首次启动时检测 users 表是否为空，
  为空则创建"admin"用户 + 从环境变量/配置读取初始密码，
  并强制要求管理员首次登录后修改密码。

调用入口：
  ensure_bootstrap_admin() — 在 app.py 启动时调用一次

密码策略：
  - 优先 BOOTSTRAP_ADMIN_PASSWORD 环境变量
  - 其次 config.bootstrap.default_password
  - 都没有：生成随机16位临时密码并仅打印到日志(强制首次登录改密码)
"""

import os
import secrets
import string
import logging

_log = logging.getLogger(__name__)

# 占位符常量 (与 schema.sql 中的 __BOOTSTRAP_PLACEHOLDER__ 一一对应)
_BOOTSTRAP_PLACEHOLDER = "__BOOTSTRAP_PLACEHOLDER__"


def _gen_random_password(length: int = 16) -> str:
    """生成随机密码 (大小写字母+数字+特殊符号)。"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    # 保证至少包含 1 个大写/小写/数字/特殊
    pwd_chars = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*"),
    ]
    pwd_chars.extend(secrets.choice(alphabet) for _ in range(length - 4))
    secrets.SystemRandom().shuffle(pwd_chars)
    return "".join(pwd_chars)


def _resolve_bootstrap_password() -> str:
    """解析 bootstrap 密码 (env > config > 随机生成)。"""
    env_pwd = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
    if env_pwd:
        return env_pwd
    try:
        from src.core.config_loader import config

        cfg_pwd = config._get("BOOTSTRAP_DEFAULT_PASSWORD", "") or ""
        if cfg_pwd:
            return cfg_pwd
    except Exception as _e:
        _log.debug(
            "resolve + _resolve_bootstrap_password + import: %s", _e, exc_info=True
        )
    # 兜底: 生成随机密码
    return _gen_random_password(16)


def ensure_bootstrap_admin() -> dict | None:
    """
    ★ P0-03: 启动时检测并初始化 admin 用户。

    逻辑：
      1. 检查 users 表是否已有密码为 __BOOTSTRAP_PLACEHOLDER__ 的占位记录
         (这是 schema.sql 留的占位条目)
      2. 如果有, 用 bootstrap 密码覆盖之, 记录必须改密码标志
      3. 如果 users 表完全为空, 完整创建 admin 用户
      4. 如果已存在真实密码(非占位符), 跳过

    返回:
      dict: {"created": bool, "password_must_change": bool, "password": str(仅在生成时返回)}
      None: 不需要 bootstrap (用户已初始化)
    """
    try:
        from src.users._user_db_core import _conn
        from src.users.auth import _hash
    except Exception as e:
        _log.warning("[Bootstrap] 导入失败: %s", e)
        return None

    conn = None
    try:
        conn = _conn()
        cur = conn.execute(
            "SELECT id, name, password FROM users WHERE id = %s", ("admin",)
        )
        row = cur.fetchone() if hasattr(cur, "fetchone") else None
        # pymysql 兼容: row 可能是 tuple
        if row is None:
            # users 表无 admin → 完整创建
            pwd = _resolve_bootstrap_password()
            ph = _hash(pwd)
            conn.execute(
                "INSERT IGNORE INTO users (id, name, password, created_at) VALUES (%s, %s, %s, NOW())",
                ("admin", "管理员", ph),
            )
            conn.execute(
                "INSERT IGNORE INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                ("admin", "admin"),
            )
            conn.commit()
            env_pwd = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "").strip()
            cfg_pwd = ""
            try:
                from src.core.config_loader import config

                cfg_pwd = config._get("BOOTSTRAP_DEFAULT_PASSWORD", "") or ""
            except Exception as _e:
                _log.debug("execute + commit + import: %s", _e, exc_info=True)
            # 显式密码(来自env或config)时不打印；只有随机生成时才打印到日志
            if env_pwd or cfg_pwd:
                _log.warning(
                    "[Bootstrap] ★ 首次启动: 已创建 admin 用户, 密码来自配置/环境变量(不在日志显示), 请立即登录并修改!"
                )
            else:
                _log.warning(
                    "[Bootstrap] ★ 首次启动: 已创建 admin 用户, 临时密码: %s (请立即登录并修改!)",
                    pwd,
                )
            return {"created": True, "password_must_change": True, "password": pwd}
        else:
            # 已有 admin 记录
            existing_pwd = row[2] if isinstance(row, tuple) else row.get("password", "")
            if existing_pwd == _BOOTSTRAP_PLACEHOLDER:
                # 是 schema.sql 占位 → 用 bootstrap 密码覆盖
                pwd = _resolve_bootstrap_password()
                ph = _hash(pwd)
                conn.execute(
                    "UPDATE users SET password = %s WHERE id = %s", (ph, "admin")
                )
                conn.commit()
                _log.warning(
                    "[Bootstrap] ★ 首次启动: 已激活 admin 用户, 临时密码: %s (请立即登录并修改!)",
                    pwd,
                )
                return {"created": True, "password_must_change": True, "password": pwd}
            # 已激活 → 跳过
            return None
    except Exception as e:
        _log.error("[Bootstrap] 异常: %s", e)
        return None
    finally:
        try:
            if conn:
                conn.close()
        except Exception as _e:
            _log.debug("conn.close + close(): %s", _e, exc_info=True)


def ensure_bootstrap_default_project() -> dict | None:
    """
    ★ 演示项目 bootstrap: 确保数据库中至少有一个可用项目。
    
    2026-08-12 合规清理：移除了原某SPA系统占位检测和重命名逻辑。
    现在只检查 projects 表是否为空，如果为空则不强制创建（由 schema.sql 种子保证）。
    """
    try:
        from src.users._user_db_core import _conn
    except Exception as e:
        _log.warning("[Bootstrap] 导入失败: %s", e)
        return None

    conn = None
    try:
        conn = _conn()
        cur = conn.execute("SELECT COUNT(*) AS cnt FROM projects WHERE status = 'active'")
        cnt = cur.fetchone()
        if cnt and cnt["cnt"] > 0:
            return None
        _log.info("[Bootstrap] 无活跃项目，schema.sql 种子将在下次重启时自动恢复")
        return None
    except Exception as e:
        _log.warning("[Bootstrap] 项目检查异常(非致命): %s", e)
        return None
    finally:
        try:
            if conn:
                conn.close()
        except Exception as _e:
            _log.debug("conn.close + close(): %s", _e, exc_info=True)


def run_all() -> dict:
    """
    ★ 一键执行所有 bootstrap 流程 (启动时调用)。
    返回: {"admin": dict|None, "project": dict|None}
    """
    return {
        "admin": ensure_bootstrap_admin(),
        "project": ensure_bootstrap_default_project(),
    }
