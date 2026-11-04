# -*- coding: utf-8 -*-
"""
覆盖率 MySQL 事实源存储 - 读取 Mixin
"""
import logging
from typing import Any

from .base import _ALL_DIMS

_log = logging.getLogger(__name__)


class _CoverageReadMixin:
    """覆盖率读取 Mixin。"""

    def load_coverage(self) -> dict[str, Any]:
        """返回最新一轮的覆盖结果（与 CoverageTracker.compute_coverage 同构）。"""
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        except Exception as _e:
            _log.debug("[read] load_coverage 异常: %s", _e, exc_info=True)
            project = ""
        try:
            from src.core.versioning import current_version_id

            active_version_id = current_version_id(project)
        except Exception as _e:
            _log.debug("[read] load_coverage 异常: %s", _e, exc_info=True)
            active_version_id = 0
        try:
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            # 取最新 run_id
            row = c.execute(
                "SELECT run_id, payload_json FROM coverage_summaries "
                "WHERE project=%s AND version_id=%s ORDER BY run_id DESC LIMIT 1",
                (project, active_version_id),
            ).fetchone()
            # [FIX] c.close() moved to finally below
            if row is None:
                return {}
            # 优先使用 payload_json（保留原始结构），否则从明细重构
            payload = self._from_json(
                (row if isinstance(row, dict) else {"payload_json": ""}).get(
                    "payload_json", ""
                ),
                None,
            )
            if payload and payload.get("matrix"):
                return payload
            # fallback: 从明细行重建
            return self._rebuild_from_details(
                project, row if isinstance(row, dict) else {"run_id": row[0]}
            )
        except Exception as e:
            self._last_error = str(e)
            _log.info("  [CoverageStore] coverage_results 读取失败: %s", str(e)[:120])
            return {}

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[read] 非关键异常: %s", _e, exc_info=True)

    def _rebuild_from_details(self, project: str, row: dict) -> dict[str, Any]:
        try:
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            run_id = (row if isinstance(row, dict) else {"run_id": row[0]}).get(
                "run_id"
            )
            rows = c.execute(
                "SELECT * FROM coverage_results WHERE project=%s AND run_id=%s ORDER BY req_id",
                (project, run_id),
            ).fetchall()
            # [FIX] c.close() moved to finally below
            matrix: dict[str, dict[str, str]] = {}
            gaps: list[dict[str, Any]] = []
            for r in rows:
                item = dict(r)
                rid = str(item.get("req_id", ""))
                dims = {
                    d: str(item.get("dim_" + d, "missing") or "missing")
                    for d in _ALL_DIMS
                }
                matrix[rid] = dims
                if int(item.get("has_gaps", 0) or 0) == 1:
                    gaps.append(
                        {
                            "req_id": rid,
                            "description": "",
                            "missing_dimensions": self._from_json(
                                item.get("gap_dims_json"), []
                            ),
                            "total_cases": int(item.get("total_cases", 0) or 0),
                        }
                    )
            return {
                "matrix": matrix,
                "gaps": gaps,
                "summary": {
                    "total_requirements": len(matrix),
                    "requirements_with_gaps": len(gaps),
                    "coverage_rate": round(
                        (len(matrix) - len(gaps)) / max(len(matrix), 1) * 100, 1
                    ),
                    "missing_by_dimension": {},
                    "total_gaps": sum(
                        len(g.get("missing_dimensions", [])) for g in gaps
                    ),
                },
                "total_requirements": len(matrix),
                "total_cases": sum(
                    int(
                        next(
                            (
                                int(item.get("total_cases", 0) or 0)
                                for item in [dict(r)]
                            ),
                            0,
                        )
                    )
                    for r in rows
                ),
            }
        except Exception as e:
            self._last_error = str(e)
            return {}

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[read] 非关键异常: %s", _e, exc_info=True)

    def has_coverage(self) -> bool:
        """当前项目是否有覆盖数据。"""
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        except Exception as _e:
            _log.debug("[read] has_coverage 异常: %s", _e, exc_info=True)
            project = ""
        try:
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            row = c.execute(
                "SELECT COUNT(*) AS cnt FROM coverage_summaries WHERE project=%s",
                (project,),
            ).fetchone()
            if row is None:
                return False
            cnt = row["cnt"] if isinstance(row, dict) else (row[0] if row else 0)
            return int(cnt or 0) > 0
        except Exception as _e:
            _log.debug("[read] has_coverage 异常: %s", _e, exc_info=True)
            return False
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)
