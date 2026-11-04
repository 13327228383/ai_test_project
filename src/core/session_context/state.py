# -*- coding: utf-8 -*-
"""
SessionStateMixin - 会话状态管理
==================================
负责：浏览器会话状态跟踪、登录检测、缓存管理。
"""

import time
import re
import logging
from typing import Any

from src.core.config_loader import config

_log = logging.getLogger(__name__)


class SessionStateMixin:
    """会话状态管理 Mixin。"""

    # --- 魔法数字常量 ---
    TABLE_LIST_MAX = 100  # for_step2 / for_traceability 表列表截断数量（默认覆盖常见业务库，避免截断）
    SCHEMA_SAMPLE_TABLES = 50  # _get_table_schema_summary 采样的表数量
    COLS_PER_TABLE_MAX = 10  # 每张表最多显示的列数
    RELATIONSHIP_SAMPLE_ROWS = 50  # 采样推断时每列取的行数
    RELATIONSHIP_SCAN_TABLES = 200  # 采样推断时扫描的表数量（默认覆盖全部，避免截断）
    MIN_SAMPLE_ROWS = 10  # 采样数据最少有效行数
    FK_MATCH_THRESHOLD = 20  # 关联判定：相同值需达到的行数
    ENUM_UNIQUE_MAX = 5  # 枚举型字段唯一值上限
    FK_DISPLAY_MAX = 20  # 外键约束显示上限
    MODULE_NAME_MAX_LEN = 40  # detected_module 截断长度

    @staticmethod
    def _sanitize_table_name(name: str) -> str:
        """净化表名：仅保留字母、数字、下划线，防止SQL注入。"""
        return re.sub(r"[^\w]", "", str(name))

    _LOGGED_IN_INDICATORS = [
        ".sidebar",
        ".side-menu",
        ".nav-menu",
        '[class*="sidebar"]',
        '[class*="nav-bar"]',
        '[class*="dashboard"]',
        '[class*="main-content"]',
        '[class*="layout"]',
        ".el-menu",
        ".ant-layout",
        '[class*="user"]',
        '[class*="avatar"]',
        '[class*="header-right"]',
        ".user-info",
    ]

    _LOGIN_PAGE_INDICATORS = [
        'input[type="password"]',
        'button:has-text("登录")',
        'button:has-text("Sign in")',
        '[class*="login"]',
        'form[class*="login"]',
    ]

    def __init__(self) -> None:
        self._page = None
        self._state_cache = {}
        self._state_cache_time = 0.0
        self._db_schema_cache = None
        self._db_connected = False
        self._cache_ttl = config.session_state_cache_seconds

    def attach_page(self, page) -> "SessionStateMixin":
        self._page = page
        self._invalidate_cache()
        return self

    def detach(self) -> None:
        self._page = None
        self._state_cache.clear()
        self._state_cache_time = 0.0
        self._db_schema_cache = None

    @staticmethod
    def reload_for_project() -> None:
        """项目切换时清理缓存并重载 DB 配置"""
        from src.extensions.db.db_agent import db_agent
        from src.extensions.db.db_core import db_executor

        # ★ 修复（2026-06-23）：本方法是 @staticmethod，无 self；原直接引用裸名 session_ctx，
        # 但 state.py 未 import 该全局（仅 __init__.py 命名空间有），导致 NameError
        # "name 'session_ctx' is not defined"，使项目切换时 session_ctx.reload_for_project() 抛异常
        # （被 api_switch_project 捕获为 "配置重载失败"）。惰性导入单例避免循环依赖。
        from src.core.session_context import session_ctx

        db_agent.reload_config()
        db_executor.reload_config()
        session_ctx._db_connected = False
        session_ctx._db_schema_cache = None
        session_ctx._state_cache.clear()
        session_ctx._state_cache_time = 0.0

    def _invalidate_cache(self) -> None:
        self._state_cache_time = 0.0
        self._state_cache.clear()
        self._db_schema_cache = None  # ★ B-007: 防止项目切换时 schema 缓存串台

    def is_logged_in(self, force_refresh: bool = False) -> bool:
        if not config.session_aware_login:
            return False
        if not force_refresh and self._state_cache:
            elapsed = time.time() - self._state_cache_time
            if elapsed < self._cache_ttl:
                return self._state_cache.get("is_logged_in", False)
        if not self._page:
            return False
        try:
            url = (self._page.url or "").lower()
            url_signal = "/login" not in url and url != "about:blank" and url != ""
            dom_signal = False
            for sel in self._LOGGED_IN_INDICATORS:
                try:
                    el = self._page.locator(sel).first
                    if el.is_visible():
                        dom_signal = True
                        break
                except Exception as e:
                    _log.debug("DOM 检测跳过 %s: %s", sel, e)
                    continue
            has_login_form = False
            for sel in self._LOGIN_PAGE_INDICATORS:
                try:
                    el = self._page.locator(sel).first
                    if el.is_visible():
                        has_login_form = True
                        break
                except Exception as e:
                    _log.debug("登录表单检测跳过 %s: %s", sel, e)
                    continue
            logged_in = (url_signal or dom_signal) and not has_login_form
            self._state_cache["is_logged_in"] = logged_in
            self._state_cache["url"] = url
            self._state_cache["has_sidebar"] = dom_signal
            self._state_cache_time = time.time()
            return logged_in
        except Exception as e:
            _log.warning("is_logged_in 检测异常: %s", e)
            return self._state_cache.get("is_logged_in", False)

    def get_state(self, force_refresh: bool = False) -> dict[str, Any]:
        if not force_refresh and self._state_cache:
            elapsed = time.time() - self._state_cache_time
            if elapsed < self._cache_ttl:
                return dict(self._state_cache)
        state = {"is_logged_in": self.is_logged_in(force_refresh=True)}
        if self._page:
            try:
                state["current_url"] = self._page.url or ""
                state["page_title"] = self._page.title() or ""
            except (AttributeError, RuntimeError) as e:
                _log.debug("get_state 页面信息获取失败: %s", e)
                state["current_url"] = ""
                state["page_title"] = ""
            try:
                module = self._page.evaluate(
                    """() => {
                    const bc = document.querySelector('.breadcrumb, [class*="breadcrumb"], .el-breadcrumb');
                    if (bc) return bc.textContent.trim().replace(/\\s+/g, ' ');
                    const active = document.querySelector('.active, .is-active, [class*="active"], [aria-current]');
                    if (active) return active.textContent.trim().replace(/\\s+/g, ' ').substring(0, %d);
                    return '';
                }"""
                    % self.MODULE_NAME_MAX_LEN
                )
                state["detected_module"] = (module or "").strip()
            except Exception as e:
                _log.debug("detected_module 检测失败: %s", e)
                state["detected_module"] = ""
        self._state_cache = state
        self._state_cache_time = time.time()
        return state
