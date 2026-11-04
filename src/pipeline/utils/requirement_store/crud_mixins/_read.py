# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""读取 mixin：list / get。"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class RequirementStoreReadMixin:
    """读取相关操作：list_requirements, get_requirement。"""

    def list_requirements(self) -> list[dict[str, Any]]:
        """读取当前项目需求镜像。"""
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project)
        c = None
        try:
            with self._lock:
                c = self._get_db()
                try:
                    self._ensure_tables(c)
                    # ★ 修复割裂：严格按 active version_id 查询，不再回退 version_id=0
                    # 回退会导致 legacy 数据混入 stats 的 requirements 计数，造成"数量与明细割裂"
                    rows = c.execute(
                        "SELECT * FROM requirements WHERE project=%s AND version_id=%s ORDER BY req_id",
                        (project, active_version_id),
                    ).fetchall()
                finally:
                    with guard("list_requirements: c.close()", log=_log, level=logging.WARNING):
                        c.close()
                self._last_error = ""
                return [self._parse_row_to_req(dict(row)) for row in rows]
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [RequirementStore] requirements 读取失败: %s", str(e)[:120])
            return []

    def get_requirement(self, req_id: str) -> dict[str, Any] | None:
        """读取单条需求镜像。不存在返回 None。"""
        req_id = str(req_id or "").strip()
        if not req_id:
            return None
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project)
        c = None
        try:
            with self._lock:
                c = self._get_db()
                try:
                    self._ensure_tables(c)
                    row = c.execute(
                        "SELECT * FROM requirements WHERE req_id=%s AND project=%s AND version_id=%s",
                        (req_id, project, active_version_id),
                    ).fetchone()
                finally:
                    with guard("get_requirement: c.close()", log=_log, level=logging.WARNING):
                        c.close()
                if not row:
                    return None
                self._last_error = ""
                return self._parse_row_to_req(dict(row))
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [RequirementStore] get_requirement 失败: %s", str(e)[:120])
            return None
