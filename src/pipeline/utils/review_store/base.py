# -*- coding: utf-8 -*-
"""
ReviewStore 基础工具 Mixin
==========================
提供数据库连接、建表、时间工具等通用方法。
"""

from datetime import datetime
import logging

_log = logging.getLogger(__name__)

ALLOWED_REVIEW_STATUS = {"pending", "approved", "rejected", "modified"}


class _ReviewBaseMixin:
    """ReviewStore 基础工具：数据库连接、建表、时间/状态工具。"""

    def __init__(self):
        self._last_error: str = ""

    @property
    def last_error(self) -> str:
        return self._last_error

    # ---------------------------------------------------------------
    # 基础工具
    # ---------------------------------------------------------------
    @staticmethod
    def _get_db():
        from src.core.db_manager import get_connection

        return get_connection()

    @staticmethod
    def _ensure_tables(c) -> None:
        """建表已统一由 src/core/schema.sql 负责，此处仅作占位以保持调用兼容。
        ★ 2026-06-22 改造：schema.sql 是 case_meta / review_log 的唯一事实源。
        - 删除原 CREATE TABLE case_meta / CREATE TABLE review_log
        - 删除原 ALTER TABLE 兼容补列逻辑（schema.sql 已包含全部列）
        - 删除原 CREATE INDEX idx_review_log_prj（schema.sql 已建索引）
        本函数保留是为了不破坏调用方 `self._ensure_tables(c)` 的签名约定。
        """
        return

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _normalize_status(status: str) -> str:
        s = str(status or "").strip().lower()
        return s if s in ALLOWED_REVIEW_STATUS else "pending"
