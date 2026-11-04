# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""健康统计 — 从 requirement_store.py 拆出，零逻辑变更。"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class _HealthMixin:
    """health_stats — 只读聚合 requirements 表。"""

    def health_stats(self, project: str | None = None) -> dict[str, Any]:
        """只读聚合当前项目 requirements 表的健康统计。

        返回字段：
          - requirement_count: int
          - generated_count: int   （is_generated != 0）
          - pending_count: int     （is_generated == 0）
          - approved_count: int    （review_status == 'approved'）
          - under_review_count: int
          - module_count: int      （去重后的 module 数量）
          - with_dependency_count: int
          - project: str
        """
        try:
            from src.core.config_loader import config as _cfg

            target_project = (
                str(project).strip()
                if project
                else (str(getattr(_cfg, "project_name", "") or "").strip())
            )
            stats: dict[str, Any] = {
                "requirement_count": 0,
                "generated_count": 0,
                "pending_count": 0,
                "approved_count": 0,
                "under_review_count": 0,
                "module_count": 0,
                "with_dependency_count": 0,
                "project": target_project,
            }
            c = self._get_db()
            try:
                self._ensure_tables(c)
                row = c.execute(
                    """
                    SELECT COUNT(*) AS total,
                           SUM(CASE WHEN is_generated != 0 THEN 1 ELSE 0 END) AS g,
                           SUM(CASE WHEN COALESCE(is_generated, 0) = 0 THEN 1 ELSE 0 END) AS p,
                           SUM(CASE WHEN review_status = 'approved' THEN 1 ELSE 0 END) AS a,
                           SUM(CASE WHEN review_status IN ('reviewing', 'pending_review', 'under_review')
                                    THEN 1 ELSE 0 END) AS ur,
                           COUNT(DISTINCT CASE WHEN COALESCE(module, '') != '' THEN module END) AS m,
                           SUM(CASE WHEN COALESCE(dependency_req_id, '') != '' THEN 1 ELSE 0 END) AS dep
                    FROM requirements WHERE project = %s
                    """,
                    (target_project,),
                ).fetchone()
                if row is not None:
                    stats["requirement_count"] = int(row["total"] or 0)
                    stats["generated_count"] = int(row["g"] or 0)
                    stats["pending_count"] = int(row["p"] or 0)
                    stats["approved_count"] = int(row["a"] or 0)
                    stats["under_review_count"] = int(row["ur"] or 0)
                    stats["module_count"] = int(row["m"] or 0)
                    stats["with_dependency_count"] = int(row["dep"] or 0)
            finally:
                with guard("health_stats: c.close()", log=_log, level=logging.WARNING):
                    c.close()
            self._last_error = ""
            return stats
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [RequirementStore] health_stats 读取失败: %s", str(e)[:120])
            return {
                "requirement_count": 0,
                "generated_count": 0,
                "pending_count": 0,
                "approved_count": 0,
                "under_review_count": 0,
                "module_count": 0,
                "with_dependency_count": 0,
                "project": str(project or ""),
                "error": str(e)[:200],
            }
