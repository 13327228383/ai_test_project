# -*- coding: utf-8 -*-
"""
AiGenerationLogsStore 基础工具 Mixin
=====================================
提供数据库连接、建表、JSON 序列化等通用方法。
"""

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)


class AiGenerationLogsStoreBaseMixin:
    """AiGenerationLogsStore 基础工具：数据库连接、建表、JSON 工具。"""

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
        """占位函数（2026-06-22：建表已交由 src/core/schema.sql 统一负责。"""
        return

    @staticmethod
    def _to_json(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception as _e:
            _log.debug("[_base] _to_json 异常: %s", _e, exc_info=True)
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
            _log.debug("[_base] _from_json 异常: %s", _e, exc_info=True)
            return default
