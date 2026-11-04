# -*- coding: utf-8 -*-
from __future__ import annotations

"""
config_session.py — 线程安全的用户/会话管理
================================================
从 config_loader.py 拆分出的独立模块，负责：
- 当前登录用户管理（thread-local）
- 会话级 API Key 管理（thread-local）
- 提供商自动识别

★ 安全拆分原则：
  - 零业务逻辑变更，仅物理位置移动
  - 保持所有导出符号 100% 一致
  - 原文件通过 re-export 保持向后兼容
"""

import logging
import threading

_log = logging.getLogger(__name__)

# =============================================================================
# 线程本地存储（确保 Flask 多线程下各用户隔离）
# =============================================================================
# ★ 背景线程（pipeline 执行器）需在启动时显式调用 set_current_user() 设置身份。

_thread_local = threading.local()


def _get_thread_local():
    """获取当前线程的本地存储，首次访问时初始化默认值。"""
    if not hasattr(_thread_local, "user_id"):
        _thread_local.user_id = None
    if not hasattr(_thread_local, "session_key"):
        _thread_local.session_key = None
    if not hasattr(_thread_local, "session_provider"):
        try:
            from src.core.config_loader import config

            _thread_local.session_provider = (
                str(config._get("ai.default_provider", "deepseek")).strip().lower()
                or "deepseek"
            )
        except Exception as _e:
            logging.getLogger(__name__).debug(
                "[config_session] session_provider 初始化失败，使用默认值 deepseek: %s",
                _e,
            )
            _thread_local.session_provider = "deepseek"
    return _thread_local


def set_current_user(user_id: str | None) -> None:
    """设置当前登录用户。AI 调用时自动使用该用户的 API Key。线程安全。"""
    _get_thread_local().user_id = user_id


def get_current_user() -> str | None:
    """获取当前登录用户 ID。线程安全。"""
    return _get_thread_local().user_id


def set_session_key(key: str) -> dict:
    """设置会话级 API Key（不落盘）。自动识别提供商。返回 {provider, key}。线程安全。"""
    tl = _get_thread_local()
    tl.session_key = key.strip() if key else ""
    tl.session_provider = _detect_provider_from_key(tl.session_key)
    return {"provider": tl.session_provider, "key": tl.session_key}


def clear_session_key():
    """清除会话级 API Key。线程安全。"""
    tl = _get_thread_local()
    tl.session_key = None
    tl.session_provider = "deepseek"


def get_session_key() -> str | None:
    """获取当前会话的 API Key。线程安全。"""
    return _get_thread_local().session_key


def ensure_session_key() -> bool:
    """确保 session_key 已设置，必要时从 DB（user_credentials_v2）自动恢复。
    返回 True 表示 session_key 已就绪，False 表示需要用户配置。
    此函数为框架级公共函数，所有 API 路由统一调用，消除重复代码。
    """
    if get_session_key():
        return True
    try:
        from src.core.config_loader import config

        db_key = config.ai_api_key
        if db_key and db_key != "local":
            set_session_key(db_key)
            return True
    except Exception as _rx1:
        _log.debug("[config_session] 异常降级(pass): %s", str(_rx1)[:60], exc_info=True)
        pass
    return False


def get_session_provider() -> str:
    """获取当前会话的提供商名称。线程安全。"""
    return _get_thread_local().session_provider


# 向后兼容：旧代码直接导入这两个全局变量名（已废弃，内部通过 thread local 代理）
# 保留 _session_key / _session_provider 作为模块级访问点，指向默认线程的值
# 注意：多线程环境下不要直接读写这些变量，请使用 getter/setter 函数。
def _get_default_tl():
    if not hasattr(_thread_local, "session_key"):
        _thread_local.session_key = None
        _thread_local.session_provider = "deepseek"
        _thread_local.user_id = None
    return _thread_local


# 模块级兼容别名（指向 thread-local 默认值，用于旧代码中的 _session_key 读取）
# 此类读取仅用于非线程敏感的初始化阶段；运行时请始终使用函数。
_session_key_backref = property(
    lambda self: _get_default_tl().session_key,
    lambda self, v: setattr(_get_default_tl(), "session_key", v),
)
_session_provider_backref = property(
    lambda self: _get_default_tl().session_provider,
    lambda self, v: setattr(_get_default_tl(), "session_provider", v),
)


def _detect_provider_from_key(key: str) -> str:
    """从 Key 前缀自动识别提供商。"""
    if not key:
        return "deepseek"
    if key.startswith("sk-ant"):
        return "anthropic"
    if key.startswith("sk-proj"):
        return "openai"
    if key.startswith("nvapi-"):
        return "nvidia"
    return "deepseek"
