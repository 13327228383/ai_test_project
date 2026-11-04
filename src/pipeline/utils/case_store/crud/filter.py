# -*- coding: utf-8 -*-
"""
CaseStore 过滤查询 mixin

职责：
- get_unexecuted_cases / get_failed_intents
"""

import json
from src.core.guarded import guard
import logging
from typing import Any

from .._helpers import _row_to_intent

_log = logging.getLogger(__name__)


class _VisFilterMixin:
    """测试用例过滤查询。"""

    def get_unexecuted_cases(self) -> list[dict[str, Any]]:
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
                    rows = c.execute(
                        """
                        SELECT tc.* FROM test_cases tc
                        LEFT JOIN (
                            SELECT case_id, MAX(executed_at) AS max_at
                            FROM executions WHERE project=%s AND version_id=%s
                            GROUP BY case_id
                        ) latest ON tc.case_id=latest.case_id
                        LEFT JOIN executions e
                          ON e.case_id=latest.case_id AND e.executed_at=latest.max_at
                        WHERE tc.project=%s AND tc.version_id=%s
                          AND (e.status IS NULL OR e.status NOT IN ('passed','success','verified'))
                        ORDER BY tc.case_id
                        """,
                        (project, active_version_id, project, active_version_id),
                    ).fetchall()
                finally:
                    if c:
                        with guard("get_unexecuted_cases: c.close()", log=_log, level=logging.WARNING):
                            c.close()
            self._last_error = ""
            result = []
            for row in rows or []:
                item = dict(row)
                try:
                    item["payload"] = json.loads(item.get("payload_json") or "{}")
                except Exception:
                    _log.warning(
                        "[Store] payload_json 解析失败, case_id=%s", item.get("case_id")
                    )
                    item["payload"] = {}
                result.append(_row_to_intent(item))
            return result
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] get_unexecuted_cases 查询失败: %s", e)
            return []

    def get_failed_intents(
        self, user_id=None, include_virtual_inherited: bool = False
    ) -> list[dict[str, Any]]:
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
                    rows = c.execute(
                        """
                        SELECT tc.* FROM test_cases tc
                        INNER JOIN (
                            SELECT case_id, MAX(executed_at) AS max_at
                            FROM executions WHERE project=%s AND version_id=%s
                            GROUP BY case_id
                        ) latest ON tc.case_id=latest.case_id
                        INNER JOIN executions e
                          ON e.case_id=latest.case_id AND e.executed_at=latest.max_at
                        WHERE tc.project=%s AND tc.version_id=%s AND UPPER(e.status) IN ('FAIL','ERROR')
                        ORDER BY tc.case_id
                        """,
                        (project, active_version_id, project, active_version_id),
                    ).fetchall()
                finally:
                    if c:
                        with guard("get_failed_intents: c.close()", log=_log, level=logging.WARNING):
                            c.close()
            self._last_error = ""
            result = []
            for row in rows or []:
                item = dict(row)
                try:
                    item["payload"] = json.loads(item.get("payload_json") or "{}")
                except Exception:
                    _log.warning(
                        "[Store] payload_json 解析失败, case_id=%s", item.get("case_id")
                    )
                    item["payload"] = {}
                result.append(_row_to_intent(item))
            return result
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] get_failed_intents 查询失败: %s", e)
            return []

    def get_unexecuted_cases_by_files(
        self, case_asset_files, user_id=None, include_virtual_inherited: bool = False
    ) -> list[dict[str, Any]]:
        """按 case_asset_file 列表读取未执行的测试用例。"""
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
                    if not case_asset_files:
                        return []
                    placeholders = ",".join(["%s"] * len(case_asset_files))
                    rows = c.execute(
                        f"""
                        SELECT vi.* FROM test_cases vi
                        WHERE vi.project=%s AND vi.version_id=%s
                          AND vi.case_asset_file IN ({placeholders})
                          AND vi.case_id NOT IN (
                            SELECT e.case_id FROM executions e
                            WHERE e.project=%s AND e.version_id=%s
                          )
                        ORDER BY vi.case_id
                        """,
                        (
                            project,
                            active_version_id,
                            *case_asset_files,
                            project,
                            active_version_id,
                        ),
                    ).fetchall()
                finally:
                    if c:
                        with guard("get_unexecuted_cases_by_files: c.close()", log=_log, level=logging.WARNING):
                            c.close()
            self._last_error = ""
            result = []
            for row in rows or []:
                item = dict(row)
                try:
                    item["payload"] = json.loads(item.get("payload_json") or "{}")
                except Exception:
                    _log.warning(
                        "[Store] payload_json 解析失败, case_id=%s", item.get("case_id")
                    )
                    item["payload"] = {}
                result.append(_row_to_intent(item))
            return result
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] get_unexecuted_cases_by_files 查询失败: %s", e)
            return []
