# -*- coding: utf-8 -*-
"""
覆盖率 MySQL 事实源存储 - 基础 Mixin
"""
import json
from typing import Any
import logging

_log = logging.getLogger(__name__)

_ALL_DIMS = [
    "positive",
    "negative",
    "boundary",
    "security",
    "api_check",
    "api_coverage",
    "db_coverage",
    "page_coverage",
    "trace_complete",
]


class _CoverageBaseMixin:
    """MySQL ``coverage_results`` 表读写工具（基础 Mixin）。"""

    def __init__(self, db_getter=None):
        self._last_error = ""
        self._db_getter = db_getter

    @property
    def last_error(self) -> str:
        return self._last_error

    # -----------------------------------------------------------------
    # DB 连接
    # -----------------------------------------------------------------
    def _get_db(self):
        if self._db_getter:
            return self._db_getter()
        from src.core.db_manager import get_connection

        return get_connection()

    @staticmethod
    def _ensure_tables(c) -> None:
        """占位函数（2026-06-22：coverage_results / coverage_summaries 表已统一由 src/core/schema.sql 负责）。"""
        return

    @staticmethod
    def _to_json(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception as _e:
            _log.debug("[base] _to_json 异常: %s", _e, exc_info=True)
            return json.dumps(str(value), ensure_ascii=False)

    @staticmethod
    def _from_json(text: Any, default: Any) -> Any:
        if text is None or text == "":
            return default
        if not isinstance(text, str):
            return text
        try:
            return json.loads(text)
        except Exception as _e:
            _log.debug("[base] _from_json 异常: %s", _e, exc_info=True)
            return default
