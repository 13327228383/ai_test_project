# -*- coding: utf-8 -*-
"""数据库配置加载模块 — 从 db_manager 拆分而来。"""

import time
from typing import Any

_config_cache: dict[str, Any] = {}
_config_ts = 0.0
_CONFIG_TTL = 30

# 必填连接参数（password 允许为空 —— 空密码是合法配置；charset 由下游给默认）
_REQUIRED_KEYS: tuple[str, ...] = ("host", "port", "user", "database")

# 配置键 → 对应环境变量名（仅用于报错提示，帮使用者一眼看出该导哪个变量）
_ENV_HINT: dict[str, str] = {
    "host": "STORAGE_MYSQL_HOST",
    "port": "STORAGE_MYSQL_PORT",
    "user": "STORAGE_MYSQL_USER",
    "database": "STORAGE_MYSQL_DATABASE",
}


class DBConfigError(RuntimeError):
    """MySQL 连接配置缺失/非法。

    ★ B3 fail-fast(2026-08-05) [已复核-DeepSeek-V4-Flash 2026-08-05]：与"连接失败"区分开。
      配置错误是**不可恢复**的（重试 5 次、退避 15 秒后仍然是同一份错配置），
      重试只会把一个 3 毫秒的清晰报错拖成 15 秒的模糊超时。
      继承 RuntimeError 以保持对既有 `except RuntimeError` 调用点的向后兼容。
    """


def _load_config() -> dict[str, Any]:
    """从 config_loader 读取 MySQL 连接参数（缓存 30s）。

    抛出:
        DBConfigError: 配置源不可读、必填项缺失，或 port 非整数。
                       调用方（get_connection）据此立即失败，不重试。
    """
    global _config_cache, _config_ts
    now = time.time()
    if _config_cache and (now - _config_ts) < _CONFIG_TTL:
        return _config_cache
    try:
        from src.core.config_loader import config as cfg

        raw: dict[str, Any] = dict(
            host=cfg._get("STORAGE_MYSQL_HOST"),
            port=cfg._get("STORAGE_MYSQL_PORT"),
            user=cfg._get("STORAGE_MYSQL_USER"),
            password=cfg._get("STORAGE_MYSQL_PASSWORD"),
            database=cfg._get("STORAGE_MYSQL_DATABASE"),
            charset=cfg._get("STORAGE_MYSQL_CHARSET"),
        )
    except Exception as e:
        raise DBConfigError(f"无法读取 MySQL 配置: {e}") from e

    # ★ B3 fail-fast [已复核-DeepSeek-V4-Flash 2026-08-05]：必填项缺失 → 立即抛，且把缺哪个/该导什么环境变量说清楚
    missing = [k for k in _REQUIRED_KEYS if raw.get(k) in (None, "")]
    if missing:
        raise DBConfigError(
            "MySQL 配置缺失: %s（请设置环境变量 %s，或在配置文件中补齐）"
            % (", ".join(missing), ", ".join(_ENV_HINT[k] for k in missing))
        )

    try:
        raw["port"] = int(raw["port"])
    except (TypeError, ValueError) as e:
        raise DBConfigError(
            "MySQL 配置非法: STORAGE_MYSQL_PORT=%r 不是整数" % (raw.get("port"),)
        ) from e

    _config_cache = raw
    _config_ts = now
    return _config_cache
