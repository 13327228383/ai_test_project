# -*- coding: utf-8 -*-
from __future__ import annotations

"""
覆盖率 MySQL 事实源存储 - 健康统计 Mixin
"""
from typing import Any
import logging

_log = logging.getLogger(__name__)


class _CoverageHealthMixin:
    """覆盖率健康统计 Mixin。"""

    def health_stats(self, project: str | None = None) -> dict[str, Any]:
        """返回 coverage_results / coverage_summaries 的只读健康统计。"""
        if project is None:
            try:
                from src.core.config_loader import config

                project = getattr(config, "project_name", "") or ""
            except Exception as _e:
                _log.debug("[health] health_stats 异常: %s", _e, exc_info=True)
                project = ""
        stats: dict[str, Any] = {
            "coverage_result_count": 0,
            "coverage_summary_count": 0,
            "total_cases": 0,
            "total_requirements": 0,
            "requirements_with_gaps": 0,
            "coverage_rate": 0.0,
            "fully_covered": 0,
            "has_gaps_count": 0,
            "dim_positive_covered": 0,
            "dim_negative_covered": 0,
            "dim_boundary_covered": 0,
            "dim_api_check_covered": 0,
            "error": None,
            "project": str(project),
        }
        try:
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            detail_cnt = c.execute(
                "SELECT COUNT(*) AS cnt FROM coverage_results WHERE project=%s",
                (project,),
            ).fetchone()
            summary_cnt = c.execute(
                "SELECT COUNT(*) AS cnt FROM coverage_summaries WHERE project=%s",
                (project,),
            ).fetchone()
            stats["coverage_result_count"] = int(
                (
                    detail_cnt["cnt"]
                    if isinstance(detail_cnt, dict)
                    else (detail_cnt[0] if detail_cnt else 0)
                )
                or 0
            )
            stats["coverage_summary_count"] = int(
                (
                    summary_cnt["cnt"]
                    if isinstance(summary_cnt, dict)
                    else (summary_cnt[0] if summary_cnt else 0)
                )
                or 0
            )
            # 从最新 summary 取汇总字段
            summary_row = c.execute(
                "SELECT total_requirements, total_cases, requirements_with_gaps, coverage_rate "
                "FROM coverage_summaries WHERE project=%s ORDER BY created_at DESC LIMIT 1",
                (project,),
            ).fetchone()
            if summary_row:
                sr = summary_row if isinstance(summary_row, dict) else dict(summary_row)
                stats["total_requirements"] = int(sr.get("total_requirements") or 0)
                stats["total_cases"] = int(sr.get("total_cases") or 0)
                stats["requirements_with_gaps"] = int(
                    sr.get("requirements_with_gaps") or 0
                )
                try:
                    stats["coverage_rate"] = float(sr.get("coverage_rate") or 0.0)
                except Exception:
                    stats["coverage_rate"] = 0.0
            # 维度覆盖统计
            for dim, key in (
                ("positive", "dim_positive_covered"),
                ("negative", "dim_negative_covered"),
                ("boundary", "dim_boundary_covered"),
                ("api_check", "dim_api_check_covered"),
            ):
                r = c.execute(
                    "SELECT COUNT(*) AS cnt FROM coverage_results "
                    "WHERE project=%s AND dim_%s='covered'" % dim,
                    (project,),
                ).fetchone()
                stats[key] = int(
                    (r["cnt"] if isinstance(r, dict) else (r[0] if r else 0)) or 0
                )
            fully = c.execute(
                "SELECT COUNT(*) AS cnt FROM coverage_results WHERE project=%s AND has_gaps=1",
                (project,),
            ).fetchone()
            stats["has_gaps_count"] = int(
                (
                    fully["cnt"]
                    if isinstance(fully, dict)
                    else (fully[0] if fully else 0)
                )
                or 0
            )
            if stats["total_requirements"] > 0:
                stats["fully_covered"] = max(
                    0, stats["total_requirements"] - stats["requirements_with_gaps"]
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
