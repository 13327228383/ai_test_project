# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""写入与清理：save_snapshot / cleanup_old_versions — 从 diff_snapshot_store.py 拆出，零逻辑变更。"""

import logging
from datetime import datetime
from typing import Any

_log = logging.getLogger(__name__)


class _WriteMixin:
    """save + cleanup 两个写操作。"""

    # -----------------------------------------------------------------
    # 写：保存快照
    # -----------------------------------------------------------------
    def save_snapshot(
        self,
        snapshot_type: str,
        data: Any,
        version_label: str | None = None,
        project: str | None = None,
    ) -> str | None:
        """保存一份版本快照到 MySQL。

        参数：
            snapshot_type: requirements / api_schema / db_schema / test_cases
            data:          快照数据（任意可 JSON 化对象）
            version_label: 版本标签；缺省时用当前时间戳
            project:       项目名；缺省时从 config 读取

        返回：成功时返回 version_label；失败返回 None。
        """
        if not snapshot_type:
            return None
        try:
            if project is None:
                from src.core.config_loader import config

                project = getattr(config, "project_name", "") or ""
        except Exception as e:
            _log.warning(
                "[DiffSnapshotStore] save_snapshot 读取 config 失败: %s",
                str(e)[: self.ERROR_MSG_TRUNCATE_LEN],
            )
            project = ""
        if version_label is None:
            try:
                from src.core.versioning import current_version_label

                active_label = current_version_label(project or "")
            except Exception as _e:
                _log.debug("[write] save_snapshot 异常: %s", _e, exc_info=True)
                active_label = ""
            version_label = active_label or datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            payload = self._to_json(data)
            item_count = self._estimate_item_count(data)
            created_at = datetime.now().isoformat()
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            c.execute(
                """
                REPLACE INTO diff_snapshots(
                    project, version_label, snapshot_type,
                    payload_json, item_count, created_at
                ) VALUES(%s, %s, %s, %s, %s, %s)
                """,
                (
                    project,
                    version_label,
                    snapshot_type,
                    payload,
                    item_count,
                    created_at,
                ),
            )
            c.commit()
            self._last_error = ""
            return version_label
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [DiffSnapshotStore] %s 写入失败: %s"
                % (snapshot_type, str(e)[: self.ERROR_MSG_TRUNCATE_LEN])
            )
            return None
        finally:
            if c is not None:
                with guard("save_snapshot: c.close()", log=_log, level=logging.WARNING):
                    c.close()

    # -----------------------------------------------------------------
    # 清理：保留最近 N 个版本
    # -----------------------------------------------------------------
    def cleanup_old_versions(
        self, keep_count: int = None, project: str | None = None
    ) -> int:
        """保留最近 N 个版本的快照，删除更旧的。返回被删除的行数。"""
        if keep_count is None:
            keep_count = self.DEFAULT_KEEP_COUNT
        if keep_count <= 0:
            return 0
        try:
            if project is None:
                from src.core.config_loader import config

                project = getattr(config, "project_name", "") or ""
        except Exception as e:
            _log.warning(
                "[DiffSnapshotStore] cleanup_old_versions 读取 config 失败: %s",
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
            all_versions = [
                r["version_label"] if isinstance(r, dict) else r[0] for r in rows
            ]
            if len(all_versions) <= keep_count:
                return 0
            to_remove = all_versions[keep_count:]
            removed = 0
            for v in to_remove:
                cur = c.execute(
                    "DELETE FROM diff_snapshots WHERE project=%s AND version_label=%s",
                    (project, v),
                )
                removed += (
                    cur.rowcount if hasattr(cur, "rowcount") and cur.rowcount else 0
                )
            c.commit()
            return removed
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [DiffSnapshotStore] cleanup_old_versions 失败: %s",
                str(e)[: self.ERROR_MSG_TRUNCATE_LEN],
            )
            return 0
        finally:
            if c is not None:
                with guard("cleanup_old_versions: c.close()", log=_log, level=logging.WARNING):
                    c.close()
