# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
ReviewStore review_log CRUD Mixin
==================================
审核操作日志的写入和读取操作。
"""

from typing import Any
import logging

_log = logging.getLogger(__name__)


class _ReviewLogMixin:
    """ReviewStore review_log 表 CRUD 操作。"""

    # -----------------------------------------------------------------
    # 版本兜底工具
    # -----------------------------------------------------------------
    @staticmethod
    def _active_version(project: str = "") -> int:
        """当前项目 active version_id（0=legacy）。"""
        from src.core.versioning import current_version_id

        return int(current_version_id(project) or 0)

    def _ensure_review_log_version_column(self, c) -> None:
        """确保 review_log 存在 version_id 列（存量库运行时 ALTER 兜底）。"""
        with guard("_ensure_review_log_version_column: _cols = { r['COLUMN_NAME'] for r in ", log=_log, level=logging.WARNING):
            _cols = {
                r["COLUMN_NAME"]
                for r in c.execute(
                    "SELECT COLUMN_NAME FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'review_log'"
                ).fetchall()
            }
            if "version_id" not in _cols:
                c.execute(
                    "ALTER TABLE review_log ADD COLUMN version_id BIGINT NOT NULL DEFAULT 0"
                )
                c.commit()

    def add_review_log(
        self,
        item_type: str,
        item_id: str,
        action: str,
        reviewer: str = "",
        comment: str = "",
        project: str = "",
    ) -> bool:
        """写入一条审核日志。"""
        item_type = str(item_type or "").strip()
        item_id = str(item_id or "").strip()
        action = str(action or "").strip()
        if not item_type or not item_id or not action:
            self._last_error = "missing required field"
            return False
        from src.core.config_loader import config

        project = project or config.project_name or ""
        try:
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            self._ensure_review_log_version_column(c)
            c.execute(
                "INSERT INTO review_log(project, version_id, item_type, item_id, action, "
                "reviewer, comment, created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    project,
                    self._active_version(project),
                    item_type,
                    item_id,
                    action,
                    str(reviewer or ""),
                    str(comment or ""),
                    self._now(),
                ),
            )
            c.commit()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [ReviewStore] review_log 写入失败: %s", str(e)[:120])
            return False

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[review_log] 非关键异常: %s", _e, exc_info=True)
