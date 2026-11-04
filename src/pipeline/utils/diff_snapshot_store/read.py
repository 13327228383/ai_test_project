# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""读取与计数：load_snapshot / load_previous_version / list_versions / count_snapshots / health_stats — 从 diff_snapshot_store.py 拆出，零逻辑变更。"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class _ReadMixin:
    """加载 + 列举 + 计数 + 健康统计。"""

    # -----------------------------------------------------------------
    # 读：加载快照
    # -----------------------------------------------------------------
    def load_snapshot(
        self,
        snapshot_type: str,
        version_label: str | None = None,
        project: str | None = None,
    ) -> Any | None:
        """加载一份快照。

        参数：
            version_label: None 表示取最新版本（按 created_at DESC）
        """
        if not snapshot_type:
            return None
        try:
            if project is None:
                from src.core.config_loader import config

                project = getattr(config, "project_name", "") or ""
        except Exception as e:
            _log.warning(
                "[DiffSnapshotStore] load_snapshot 读取 config 失败: %s",
                str(e)[: self.ERROR_MSG_TRUNCATE_LEN],
            )
            project = ""
        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            if version_label:
                row = c.execute(
                    "SELECT payload_json FROM diff_snapshots "
                    "WHERE project=%s AND version_label=%s AND snapshot_type=%s",
                    (project, version_label, snapshot_type),
                ).fetchone()
            else:
                try:
                    from src.core.versioning import current_version_label

                    active_label = current_version_label(project or "")
                except Exception as _e:
                    _log.debug("[read] 非关键异常: %s", _e, exc_info=True)
                    active_label = ""
                row = None
                if active_label:
                    row = c.execute(
                        "SELECT payload_json FROM diff_snapshots "
                        "WHERE project=%s AND version_label=%s AND snapshot_type=%s",
                        (project, active_label, snapshot_type),
                    ).fetchone()
                if row is None:
                    row = c.execute(
                        "SELECT payload_json FROM diff_snapshots "
                        "WHERE project=%s AND snapshot_type=%s "
                        "ORDER BY created_at DESC LIMIT 1",
                        (project, snapshot_type),
                    ).fetchone()
            if row is None:
                self._last_error = "未找到快照: project=%s, type=%s, version=%s" % (
                    project,
                    snapshot_type,
                    version_label,
                )
                return None
            payload = (
                row["payload_json"]
                if isinstance(row, dict)
                else (row[0] if row else "")
            )
            self._last_error = ""
            return self._from_json(payload, None)
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [DiffSnapshotStore] %s 读取失败: %s"
                % (snapshot_type, str(e)[: self.ERROR_MSG_TRUNCATE_LEN])
            )
            return None
        finally:
            if c is not None:
                with guard("load_snapshot: c.close()", log=_log, level=logging.WARNING):
                    c.close()

    def load_previous_version(
        self, snapshot_type: str, exclude_version: str, project: str | None = None
    ) -> Any | None:
        """加载除 ``exclude_version`` 外的最新版本快照。"""
        if not snapshot_type:
            return None
        try:
            if project is None:
                from src.core.config_loader import config

                project = getattr(config, "project_name", "") or ""
        except Exception as e:
            _log.warning(
                "[DiffSnapshotStore] load_previous_version 读取 config 失败: %s",
                str(e)[: self.ERROR_MSG_TRUNCATE_LEN],
            )
            project = ""
        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            row = c.execute(
                "SELECT payload_json FROM diff_snapshots "
                "WHERE project=%s AND snapshot_type=%s AND version_label!=%s "
                "ORDER BY created_at DESC LIMIT 1",
                (project, snapshot_type, exclude_version),
            ).fetchone()
            if row is None:
                self._last_error = "未找到可用的历史快照 (exclude=%s)" % exclude_version
                return None
            payload = (
                row["payload_json"]
                if isinstance(row, dict)
                else (row[0] if row else "")
            )
            return self._from_json(payload, None)
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [DiffSnapshotStore] load_previous_version 失败: %s",
                str(e)[: self.ERROR_MSG_TRUNCATE_LEN],
            )
            return None
        finally:
            if c is not None:
                with guard("load_previous_version: c.close()", log=_log, level=logging.WARNING):
                    c.close()

    def list_versions(self, project: str | None = None) -> list[str]:
        """返回按时间倒序的全部版本标签（去重）。"""
        try:
            if project is None:
                from src.core.config_loader import config

                project = getattr(config, "project_name", "") or ""
        except Exception as e:
            _log.warning(
                "[DiffSnapshotStore] list_versions 读取 config 失败: %s",
                str(e)[: self.ERROR_MSG_TRUNCATE_LEN],
            )
            project = ""
        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            rows = c.execute(
                "SELECT DISTINCT version_label FROM diff_snapshots "
                "WHERE project=%s ORDER BY version_label DESC",
                (project,),
            ).fetchall()
            return [r["version_label"] if isinstance(r, dict) else r[0] for r in rows]
        except Exception as e:
            self._last_error = str(e)
            _log.warning(
                "[DiffSnapshotStore] list_versions 查询失败: %s",
                str(e)[: self.ERROR_MSG_TRUNCATE_LEN],
            )
            return []
        finally:
            if c is not None:
                with guard("list_versions: c.close()", log=_log, level=logging.WARNING):
                    c.close()

    def count_snapshots(
        self, snapshot_type: str | None = None, project: str | None = None
    ) -> int:
        """返回快照数量（按类型过滤）。"""
        try:
            if project is None:
                from src.core.config_loader import config

                project = getattr(config, "project_name", "") or ""
        except Exception as e:
            _log.warning(
                "[DiffSnapshotStore] count_snapshots 读取 config 失败: %s",
                str(e)[: self.ERROR_MSG_TRUNCATE_LEN],
            )
            project = ""
        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            if snapshot_type:
                row = c.execute(
                    "SELECT COUNT(*) AS cnt FROM diff_snapshots "
                    "WHERE project=%s AND snapshot_type=%s",
                    (project, snapshot_type),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT COUNT(*) AS cnt FROM diff_snapshots WHERE project=%s",
                    (project,),
                ).fetchone()
            if row is None:
                return 0
            cnt = row["cnt"] if isinstance(row, dict) else (row[0] if row else 0)
            return int(cnt or 0)
        except Exception as e:
            self._last_error = str(e)
            _log.warning(
                "[DiffSnapshotStore] count_snapshots 查询失败: %s",
                str(e)[: self.ERROR_MSG_TRUNCATE_LEN],
            )
            return 0
        finally:
            if c is not None:
                with guard("count_snapshots: c.close()", log=_log, level=logging.WARNING):
                    c.close()

    # -----------------------------------------------------------------
    # Health stats（供 consistency_checker 使用）
    # -----------------------------------------------------------------
    def health_stats(self, project: str | None = None) -> dict[str, int]:
        """返回各 snapshot_type 的计数。"""
        try:
            if project is None:
                from src.core.config_loader import config

                project = getattr(config, "project_name", "") or ""
        except Exception as e:
            _log.warning(
                "[DiffSnapshotStore] health_stats 读取 config 失败: %s",
                str(e)[: self.ERROR_MSG_TRUNCATE_LEN],
            )
            project = ""
        result: dict[str, int] = {t: 0 for t in self.KNOWN_TYPES}
        result["total"] = 0
        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            rows = c.execute(
                "SELECT snapshot_type, COUNT(*) AS cnt FROM diff_snapshots "
                "WHERE project=%s GROUP BY snapshot_type",
                (project,),
            ).fetchall()
            for r in rows:
                s_type = r["snapshot_type"] if isinstance(r, dict) else r[0]
                cnt = r["cnt"] if isinstance(r, dict) else r[1]
                if s_type in result:
                    result[s_type] = int(cnt or 0)
                result["total"] += int(cnt or 0)
            return result
        except Exception as e:
            self._last_error = str(e)
            _log.warning(
                "[DiffSnapshotStore] health_stats 查询失败: %s",
                str(e)[: self.ERROR_MSG_TRUNCATE_LEN],
            )
            return result
        finally:
            if c is not None:
                with guard("health_stats: c.close()", log=_log, level=logging.WARNING):
                    c.close()
