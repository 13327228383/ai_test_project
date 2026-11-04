# -*- coding: utf-8 -*-
"""
ActionLibraryStore 基础工具 Mixin
==================================
提供数据库连接、建表、JSON 序列化等通用方法。
"""

from datetime import datetime
from typing import Any
import json
import logging

_log = logging.getLogger(__name__)


class _ActionLibraryBaseMixin:
    """ActionLibraryStore 基础工具：数据库连接、建表、时间/JSON 工具。"""

    def __init__(self, db_getter=None, project_getter=None):
        self._last_error: str = ""
        self._db_getter = db_getter
        self._project_getter = project_getter

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    @property
    def last_error(self) -> str:
        return self._last_error

    def _get_db(self):
        if self._db_getter:
            return self._db_getter()
        from src.core.db_manager import get_connection

        return get_connection()

    def _get_project(self) -> str:
        """兼容旧测试/调用方；通用指令库不再使用项目维度。"""
        if self._project_getter:
            return str(self._project_getter() or "")
        try:
            from src.core.config_loader import config

            return str(getattr(config, "project_name", "") or "")
        except Exception as _e:
            _log.debug("[base] _get_project 异常: %s", _e, exc_info=True)
            return ""

    @staticmethod
    def _ensure_tables(c) -> None:
        """占位函数（2026-06-22：action_categories / action_actions 表已统一由 src/core/schema.sql 负责）。"""
        return

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
