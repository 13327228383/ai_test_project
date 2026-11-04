# -*- coding: utf-8 -*-
"""
覆盖率 MySQL 事实源存储 - 写入 Mixin
"""
import logging
from datetime import datetime
from typing import Any

_log = logging.getLogger(__name__)


class _CoverageWriteMixin:
    """覆盖率写入 Mixin。"""

    def upsert_coverage(self, coverage_result: dict[str, Any]) -> bool:
        """把覆盖矩阵写入 MySQL。

        参数 coverage_result 格式与 CoverageTracker.compute_coverage() 返回一致：
            {"matrix": {req_id: {dim: status}}, "gaps": [...], "summary": {...},
             "total_requirements": N, "total_cases": N}
        """
        if not coverage_result:
            return True
        matrix = coverage_result.get("matrix") or {}
        if not matrix:
            return True
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        except Exception as _e:
            _log.debug("[write] upsert_coverage 异常: %s", _e, exc_info=True)
            project = ""
        try:
            from src.core.versioning import current_version_id

            active_version_id = current_version_id(project)
        except Exception as _e:
            _log.debug("[write] upsert_coverage 异常: %s", _e, exc_info=True)
            active_version_id = 0
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary = coverage_result.get("summary") or {}
        gaps_list = coverage_result.get("gaps") or []

        # 按 req_id 聚合缺口 → {req_id: [dim1, dim2]}
        gap_by_req: dict[str, list[str]] = {}
        for g in gaps_list:
            rid = str(g.get("req_id", "") or "").strip()
            if rid:
                gap_by_req[rid] = g.get("missing_dimensions") or []

        try:
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            # 先清空当前项目旧的明细，保留最新 run_id
            c.execute(
                "DELETE FROM coverage_results WHERE project=%s AND version_id=%s",
                (project, active_version_id),
            )
            for rid, dims in matrix.items():
                req_id = str(rid).strip()
                if not req_id:
                    continue
                has_gaps_int = 1 if gap_by_req.get(req_id) else 0
                gap_dims = gap_by_req.get(req_id, [])
                c.execute(
                    """
                    REPLACE INTO coverage_results(
                        project, version_id, run_id, req_id,
                        dim_positive, dim_negative, dim_boundary, dim_security, dim_api_check,
                        dim_api_coverage, dim_db_coverage, dim_page_coverage, dim_trace_complete,
                        total_cases, has_gaps, gap_dims_json, payload_json,
                        created_at, updated_at
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        project,
                        active_version_id,
                        run_id,
                        req_id,
                        str(dims.get("positive", "missing") or "missing"),
                        str(dims.get("negative", "missing") or "missing"),
                        str(dims.get("boundary", "missing") or "missing"),
                        str(dims.get("security", "missing") or "missing"),
                        str(dims.get("api_check", "missing") or "missing"),
                        str(dims.get("api_coverage", "n/a") or "n/a"),
                        str(dims.get("db_coverage", "n/a") or "n/a"),
                        str(dims.get("page_coverage", "n/a") or "n/a"),
                        str(dims.get("trace_complete", "n/a") or "n/a"),
                        next(
                            (
                                int(g["total_cases"])
                                for g in gaps_list
                                if str(g.get("req_id", "")) == req_id
                            ),
                            0,
                        ),
                        has_gaps_int,
                        self._to_json(gap_dims),
                        self._to_json(
                            {"req_id": req_id, "dims": dict(dims), "gaps": gap_dims}
                        ),
                        now,
                        now,
                    ),
                )
            # 摘要表：先清旧的，再写最新的
            c.execute(
                "DELETE FROM coverage_summaries WHERE project=%s AND version_id=%s",
                (project, active_version_id),
            )
            c.execute(
                """
                REPLACE INTO coverage_summaries(
                    project, version_id, run_id, total_requirements, total_cases,
                    requirements_with_gaps, coverage_rate, total_gaps,
                    missing_by_dim_json, payload_json, created_at
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    project,
                    active_version_id,
                    run_id,
                    int(coverage_result.get("total_requirements", 0) or 0),
                    int(coverage_result.get("total_cases", 0) or 0),
                    int(summary.get("requirements_with_gaps", 0) or 0),
                    float(summary.get("coverage_rate", 0.0) or 0.0),
                    int(summary.get("total_gaps", 0) or 0),
                    self._to_json(summary.get("missing_by_dimension", {}) or {}),
                    self._to_json(coverage_result),
                    now,
                ),
            )
            c.commit()
            # [FIX] c.close() moved to finally below
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] 覆盖率写入失败: %s", e)
            _log.info("  [CoverageStore] coverage_results 写入失败: %s", str(e)[:120])
            return False

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[write] 非关键异常: %s", _e, exc_info=True)

    def clear_project_coverage(self) -> bool:
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        except Exception as _e:
            _log.debug("[write] clear_project_coverage 异常: %s", _e, exc_info=True)
            project = ""
        try:
            from src.core.versioning import current_version_id

            active_version_id = current_version_id(project)
        except Exception as _e:
            _log.debug("[write] clear_project_coverage 异常: %s", _e, exc_info=True)
            active_version_id = 0
        try:
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            c.execute(
                "DELETE FROM coverage_results WHERE project=%s AND version_id=%s",
                (project, active_version_id),
            )
            c.execute(
                "DELETE FROM coverage_summaries WHERE project=%s AND version_id=%s",
                (project, active_version_id),
            )
            c.commit()
            # [FIX] c.close() moved to finally below
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [CoverageStore] coverage_results 清空失败: %s", str(e)[:120])
            return False

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[write] 非关键异常: %s", _e, exc_info=True)
