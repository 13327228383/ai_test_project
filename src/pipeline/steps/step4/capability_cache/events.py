# -*- coding: utf-8 -*-
from __future__ import annotations

"""CapabilityCache 的事件记录/项目策略/容量裁剪 — 从 capability_cache.py 拆出，零逻辑变更。"""

import json
from typing import Any

from .keys import _active_version_id


class _CapabilityCacheEventsMixin:
    """事件流水 + 项目策略 + 容量裁剪。"""

    def _record_event(
        self,
        conn,
        project: str,
        capability_key: str,
        capability_name: str,
        event_type: str,
        reason: str = "",
        old_signature_hash: str = "",
        new_signature_hash: str = "",
        context: dict[str, Any] | None = None,
    ) -> None:
        """能力缓存产生的数据统一写入事件表，不只写日志。"""
        conn.execute(
            """
            INSERT INTO capability_cache_events(
                project, version_id, capability_key, capability_name, event_type, reason,
                old_signature_hash, new_signature_hash, context_json, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            """,
            (
                project,
                _active_version_id(project),
                capability_key,
                capability_name,
                event_type,
                reason,
                old_signature_hash or "",
                new_signature_hash or "",
                json.dumps(context or {}, ensure_ascii=False, default=str),
            ),
        )

    def _ensure_policy(self, conn, project: str) -> None:
        """确保每个项目都有治理策略。策略本身也是项目级数据。"""
        conn.execute(
            """
            INSERT INTO capability_cache_policy(
                project, max_capabilities, event_retention_days, created_at, updated_at
            ) VALUES(%s,%s,%s,NOW(),NOW())
            ON DUPLICATE KEY UPDATE updated_at=updated_at
            """,
            (project, self._MAX_PER_PROJECT, self._EVENT_RETENTION_DAYS),
        )

    def _load_policy(self, conn, project: str) -> dict[str, int]:
        row = conn.execute(
            """
            SELECT max_capabilities, event_retention_days
            FROM capability_cache_policy WHERE project=%s
            """,
            (project,),
        ).fetchone()
        if not row:
            return {
                "max_capabilities": self._MAX_PER_PROJECT,
                "event_retention_days": self._EVENT_RETENTION_DAYS,
            }
        return {
            "max_capabilities": int(
                row.get("max_capabilities") or self._MAX_PER_PROJECT
            ),
            "event_retention_days": int(
                row.get("event_retention_days") or self._EVENT_RETENTION_DAYS
            ),
        }

    def _prune_project(self, conn, project: str) -> None:
        """按项目清理：事件只保留最近3天；能力缓存按项目容量上限淘汰低价值记录。"""
        policy = self._load_policy(conn, project)
        max_capabilities = max(
            100, int(policy.get("max_capabilities") or self._MAX_PER_PROJECT)
        )
        retention_days = max(
            1, int(policy.get("event_retention_days") or self._EVENT_RETENTION_DAYS)
        )
        conn.execute(
            """
            DELETE FROM capability_cache_events
            WHERE project=%s AND created_at < DATE_SUB(NOW(), INTERVAL %s DAY)
            """,
            (project, retention_days),
        )
        conn.execute(
            """
            DELETE FROM capability_cache
            WHERE project=%s
              AND capability_key NOT IN (
                SELECT capability_key FROM (
                    SELECT capability_key
                    FROM capability_cache
                    WHERE project=%s
                    ORDER BY is_valid DESC, success_count DESC, hit_count DESC, updated_at DESC
                    LIMIT %s
                ) AS keepers
              )
            """,
            (project, project, max_capabilities),
        )
