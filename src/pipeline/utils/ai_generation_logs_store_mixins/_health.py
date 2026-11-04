# -*- coding: utf-8 -*-
from __future__ import annotations

"""
AiGenerationLogsStore 健康检查 Mixin
=====================================
提供 ``health_stats`` 只读健康统计方法，供 consistency_checker 调用。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class AiGenerationLogsStoreHealthMixin:
    """AiGenerationLogsStore 健康检查能力。"""

    # -----------------------------------------------------------------
    # 一致性健康检查：供 consistency_checker --db-health 调用
    # -----------------------------------------------------------------
    def health_stats(self, project: str | None = None) -> dict[str, Any]:
        """返回 ai_generation_logs 表的只读健康统计。"""
        if project is None:
            try:
                from src.core.config_loader import config

                project = getattr(config, "project_name", "") or ""
            except Exception as _e:
                _log.debug("[_health] health_stats 异常: %s", _e, exc_info=True)
                project = ""
        stats: dict[str, Any] = {
            "log_count": 0,
            "hallucination_count": 0,
            "env_count": 0,
            "unknown_count": 0,
            "distinct_case_ids": 0,
            "distinct_modules": 0,
            "latest_created_at": None,
            "error": None,
            "project": str(project),
        }
        try:
            c = None
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            total = c.execute(
                "SELECT COUNT(*) AS cnt FROM ai_generation_logs WHERE project=%s",
                (project,),
            ).fetchone()
            stats["log_count"] = int(
                (
                    total["cnt"]
                    if isinstance(total, dict)
                    else (total[0] if total else 0)
                )
                or 0
            )
            for cls in ("hallucination", "env", "unknown"):
                r = c.execute(
                    "SELECT COUNT(*) AS cnt FROM ai_generation_logs WHERE project=%s AND classification=%s",
                    (project, cls),
                ).fetchone()
                key = "%s_count" % cls
                stats[key] = int(
                    (r["cnt"] if isinstance(r, dict) else (r[0] if r else 0)) or 0
                )
            dc = c.execute(
                "SELECT COUNT(DISTINCT case_id) AS cnt FROM ai_generation_logs WHERE project=%s",
                (project,),
            ).fetchone()
            stats["distinct_case_ids"] = int(
                (dc["cnt"] if isinstance(dc, dict) else (dc[0] if dc else 0)) or 0
            )
            dm = c.execute(
                "SELECT COUNT(DISTINCT module) AS cnt FROM ai_generation_logs WHERE project=%s",
                (project,),
            ).fetchone()
            stats["distinct_modules"] = int(
                (dm["cnt"] if isinstance(dm, dict) else (dm[0] if dm else 0)) or 0
            )
            latest = c.execute(
                "SELECT created_at FROM ai_generation_logs WHERE project=%s "
                "ORDER BY created_at DESC LIMIT 1",
                (project,),
            ).fetchone()
            if latest:
                stats["latest_created_at"] = (
                    latest["created_at"]
                    if isinstance(latest, dict)
                    else (latest[0] if latest else None)
                )
        except Exception as e:
            self._last_error = str(e)
            stats["error"] = "health_stats_failed: %s" % str(e)[:200]
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)
        return stats
