# -*- coding: utf-8 -*-
"""
_FigmaCacheBaseMixin — 基础方法：初始化、连接、锁、建表、键生成。
"""

import threading
from pathlib import Path
import logging

_log = logging.getLogger(__name__)


class _FigmaCacheBaseMixin:
    """基础方法 mixin：初始化、数据库连接、建表、键生成。"""

    _TABLE = "figma_mcp_cache"

    def __init__(self, cache_path: Path = None):
        self._db_path = str(cache_path) if cache_path else ""
        self._conn = None
        self._lock = threading.Lock()
        self._init_db()

    def _ensure_conn(self):
        # ★ Fix: 连接池泄漏修复 — 检查连接是否已关闭（已被池回收），若否则复用，若是则重新获取
        if self._conn is not None:
            try:
                # ping 检测连接是否还活着
                if hasattr(self._conn, "_conn"):
                    _raw = self._conn._conn
                else:
                    _raw = self._conn
                if hasattr(_raw, "ping"):
                    _raw.ping(reconnect=True)
                return
            except Exception:
                # 连接已失效，重新获取
                try:
                    self._conn.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)
                self._conn = None
        from src.core.db_manager import get_connection

        self._conn = get_connection()

    @staticmethod
    def _scope():
        """Figma MCP 缓存按项目/版本隔离，避免不同项目或版本的设计稿响应互相污染。"""
        try:
            from src.core.config_loader import config

            project = str(getattr(config, "project_name", "") or "").strip()
            version = str(getattr(config, "project_version", "") or "").strip()
        except Exception:
            project, version = "", ""
        return project, version

    def _init_db(self):
        self._ensure_conn()
        # ★ 2026-06-22：建表已统一由 src/core/schema.sql 负责，此处仅占位以保持调用兼容。
        return

    def _ensure_project_columns(self):
        """占位函数（2026-06-22：列已统一由 src/core/schema.sql 负责）。"""
        return

    def _make_key(self, file_key: str, node_id: str = "", depth: int = 0) -> str:
        project, version = self._scope()
        return "%s:%s:%s:%s:%d" % (project, version, file_key, node_id, depth)
