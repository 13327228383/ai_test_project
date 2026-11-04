# -*- coding: utf-8 -*-
"""
SourceHashesStore 基础 mixin：__init__、last_error、_project、_get_db、
_ensure_tables、_to_json、_from_json 以及模块级常量。
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
_HASH_VALUE_MAX_LEN = 255  # VARCHAR(255) 上限 & hash_value 截断长度
_PAGE_KEY_PREFIX = "page:"  # page_id 行的 hash_key 前缀
_OVERALL_KEY = "__overall__"  # overall 行的 hash_key


# ---------------------------------------------------------------------------
# _SourceHashesBaseMixin
# ---------------------------------------------------------------------------
class _SourceHashesBaseMixin:
    """SourceHashesStore 的基础能力：DB 连接、建表、JSON 序列化 / 反序列化。"""

    def __init__(self, db_getter=None):
        self._last_error = ""
        self._db_getter = db_getter

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    @property
    def last_error(self) -> str:
        return self._last_error

    def _project(self) -> str:
        try:
            from src.core.config_loader import config

            return getattr(config, "project_name", "") or ""
        except Exception as exc:
            logger.warning("Failed to resolve project_name: %s", exc)
            return ""

    def _get_db(self):
        if self._db_getter:
            return self._db_getter()
        try:
            from src.core.db_manager import get_connection

            return get_connection()
        except Exception as e:
            self._last_error = "connect_failed: %s" % str(e)
            raise

    @staticmethod
    def _ensure_tables(c) -> None:
        """占位函数（2026-06-22：source_*_hashes 三张表已统一由 src/core/schema.sql 负责）。"""
        return

    @staticmethod
    def _to_json(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.warning("Failed to serialize value to JSON: %s", exc)
            return json.dumps(str(value), ensure_ascii=False)

    @staticmethod
    def _from_json(text: Any, default: Any) -> Any:
        if text is None or text == "":
            return default
        if not isinstance(text, str):
            return text
        try:
            return json.loads(text)
        except Exception as exc:
            logger.warning("Failed to parse JSON text: %s", exc)
            return default
