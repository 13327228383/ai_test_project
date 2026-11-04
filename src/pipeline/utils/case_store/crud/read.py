# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
CaseStore 读取 mixin

职责：
- get_intent / list_intents / list_intents_with_meta
- get_intents_by_ids / get_intents_by_files
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

from .._helpers import _row_to_intent

_log = logging.getLogger(__name__)

# ★ 2026-07-13: list_intents_with_meta 5 秒 TTL 内存缓存
# 一次控制台加载会调用 3 次（stats / pipeline-progress / intent-metrics），
# 全表 JOIN test_cases+case_meta 较重；5 秒 TTL 避免重复查询。
# 按 (project, version_id, offset, limit, case_asset_file, include_virtual_inherited) 隔离
_intents_meta_cache: dict[tuple, tuple[float, list]] = {}
_INTENTS_META_TTL = 5  # 秒


class _VisReadMixin:
    """测试用例读取操作。"""

    def get_intent(self, case_id: str) -> dict[str, Any] | None:
        case_id = str(case_id or "").strip()
        if not case_id:
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
                        "SELECT * FROM test_cases WHERE case_id=%s AND project=%s AND version_id=%s",
                        (case_id, project, active_version_id),
                    ).fetchone()
                    if not row:
                        inherited = self._load_virtual_inherited_rows(
                            c, project, active_version_id, case_ids=[case_id]
                        )
                        row = inherited[0] if inherited else None
                finally:
                    if c:
                        with guard("get_intent: c.close()", log=_log, level=logging.WARNING):
                            c.close()
            if not row:
                return None
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload_json") or "{}")
            except Exception:
                _log.warning(
                    "[Store] payload_json 解析失败, case_id=%s", item.get("case_id")
                )
                item["payload"] = {}
            return _row_to_intent(item)
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] get_intent 失败: %s", e)
            return None

    def list_intents(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        case_asset_file: str | None = None,
        include_virtual_inherited: bool = True,
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
                    sql = "SELECT * FROM test_cases WHERE project=%s AND version_id=%s AND valid_status <> 'stale'"
                    params = [project, active_version_id]
                    if case_asset_file:
                        sql += " AND case_asset_file=%s"
                        params.append(str(case_asset_file))
                    sql += " ORDER BY case_id"
                    if limit is not None:
                        sql += " LIMIT %s OFFSET %s"
                        params.extend([int(limit), int(offset)])
                    elif offset > 0:
                        sql += " LIMIT %s OFFSET %s"
                        params.extend([999999, int(offset)])
                    rows = c.execute(sql, tuple(params)).fetchall()
                    inherited_rows = []
                    if include_virtual_inherited and limit is None and offset == 0:
                        inherited_rows = self._load_virtual_inherited_rows(
                            c,
                            project,
                            active_version_id,
                            case_asset_file=case_asset_file,
                        )
                finally:
                    if c:
                        with guard("list_intents: c.close()", log=_log, level=logging.WARNING):
                            c.close()
            self._last_error = ""
            result = []
            for row in (rows or []) + (inherited_rows or []):
                item = dict(row)
                try:
                    item["payload"] = json.loads(item.get("payload_json") or "{}")
                except Exception:
                    _log.warning(
                        "[Store] payload_json 解析失败, case_id=%s", item.get("case_id")
                    )
                    item["payload"] = {}
                result.append(_row_to_intent(item))
            return self._attach_execution_status(result, project, active_version_id)
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] list_intents 读取失败: %s", e)
            return []

    def list_intents_with_meta(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
        case_asset_file: str | None = None,
        include_virtual_inherited: bool = True,
    ) -> list[dict[str, Any]]:
        from src.core.config_loader import config

        project = config.project_name or ""
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project)
        # ★ 2026-07-13: 5 秒 TTL 缓存命中检查
        cache_key = (
            project,
            active_version_id,
            offset,
            limit,
            case_asset_file,
            include_virtual_inherited,
        )
        _cached = _intents_meta_cache.get(cache_key)
        if _cached and (time.time() - _cached[0]) < _INTENTS_META_TTL:
            return _cached[1]
        c = None
        try:
            with self._lock:
                c = self._get_db()
                try:
                    self._ensure_tables(c)
                    sql = """
                        SELECT tc.*,
                               cm.confidence AS _confidence,
                               cm.risk_level AS _risk_level,
                               cm.review_status AS _review_status,
                               cm.case_coverage AS _case_coverage,
                               cm.case_completeness AS _case_completeness,
                               cm.evidence_total AS _evidence_total,
                               cm.avg_confidence AS _avg_confidence,
                               cm.case_driven_rate AS _case_driven_rate,
                               cm.strictness_score AS _strictness_score,
                               cm.execution_factors AS _execution_factors,
                               cm.evidence_confidence AS _evidence_confidence,
                               cm.evidence_ai_raw AS _evidence_ai_raw,
                               cm.evidence_cal_accuracy AS _evidence_cal_accuracy,
                               cm.evidence_strength AS _evidence_strength,
                               cm.evidence_level AS _evidence_level,
                               cm.evidence_verdict AS _evidence_verdict,
                               cm.evidence_cold_start AS _evidence_cold_start
                        FROM test_cases tc
                        LEFT JOIN case_meta cm
                          ON tc.case_id = cm.case_id AND tc.project = cm.project AND tc.version_id = cm.version_id
                        WHERE tc.project=%s AND tc.version_id=%s AND tc.valid_status NOT IN ('stale', 'retired')
                        """
                    params = [project, active_version_id]
                    if case_asset_file:
                        sql += " AND tc.case_asset_file=%s"
                        params.append(str(case_asset_file))
                    sql += " ORDER BY tc.case_id"
                    if limit is not None:
                        sql += " LIMIT %s OFFSET %s"
                        params.extend([int(limit), int(offset)])
                    elif offset > 0:
                        sql += " LIMIT %s OFFSET %s"
                        params.extend([999999, int(offset)])
                    rows = c.execute(sql, tuple(params)).fetchall()
                    inherited_rows = []
                    if include_virtual_inherited and limit is None and offset == 0:
                        inherited_rows = self._load_virtual_inherited_rows(
                            c,
                            project,
                            active_version_id,
                            case_asset_file=case_asset_file,
                        )
                finally:
                    if c:
                        with guard("list_intents_with_meta: c.close()", log=_log, level=logging.WARNING):
                            c.close()
            self._last_error = ""
            result = []
            for row in (rows or []) + (inherited_rows or []):
                item = dict(row)
                try:
                    item["payload"] = json.loads(item.get("payload_json") or "{}")
                except Exception:
                    _log.warning(
                        "[Store] payload_json 解析失败, case_id=%s", item.get("case_id")
                    )
                    item["payload"] = {}
                intent = _row_to_intent(item)
                for key in (
                    "_confidence",
                    "_risk_level",
                    "_review_status",
                    "_case_coverage",
                    "_case_completeness",
                    "_evidence_total",
                    "_avg_confidence",
                    "_case_driven_rate",
                    "_strictness_score",
                    "_execution_factors",
                    "_evidence_confidence",
                    "_evidence_ai_raw",
                    "_evidence_cal_accuracy",
                    "_evidence_strength",
                    "_evidence_level",
                    "_evidence_verdict",
                    "_evidence_cold_start",
                ):
                    if item.get(key) not in (None, ""):
                        intent[key] = item.get(key)
                result.append(intent)
            final_result = self._attach_execution_status(
                result, project, active_version_id
            )
            # ★ 2026-07-13: 写入 5 秒 TTL 缓存（仅缓存成功结果）
            _intents_meta_cache[cache_key] = (time.time(), final_result)
            return final_result
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] list_intents_with_meta 读取失败: %s", e)
            return []

    def compute_case_metrics(self) -> dict[str, Any]:
        """★ 聚合意图驱动指标（供 /api/intent-metrics 使用）。

        口径与审核面板 (case_review/_routes.py:api_intents_review_list) 完全一致，
        消除"同一指标 KPI 面板/审核面板两套数字"的缝合怪：
        - total = 全部意图数（含驱动率为 0 的，与审核面板同口径）
        - 各指标 = 全部意图对应字段的均值（sum / max(total, 1)）
        - has_data = 至少一条意图 _case_driven_rate > 0（即经 Step3 质量评估有数据）
        - has_data=False（无意图 / 意图未经 Step3 评估）时各指标返回 None，
          前端渲染 '—' 不告警，避免"意图未执行/未验证却报 0% 不达标"的误报。
        """
        empty: dict[str, Any] = {
            "has_data": False,
            "total": 0,
            "case_driven_rate": None,
            "strictness_score": None,
            "case_completeness": None,
            "evidence_total": None,
        }
        try:
            intents = self.list_intents_with_meta()
        except Exception as e:
            _log.error("[Store] compute_case_metrics 读取失败: %s", e)
            return empty

        # 整改 B3.1：原内联 `_safe_float(c, key)` 副本已收敛到 src/core/utils/num.py
        # 单一真源（保留私名与 (mapping, key) 签名，下方全部调用点零改动）。
        from src.core.utils.num import safe_float_of as _safe_float

        total = len(intents)
        # 与审核面板一致：仅有驱动率数据 > 0 的意图才算"有数据"
        has_data = total > 0 and any(
            _safe_float(c, "_case_driven_rate") > 0 for c in intents
        )
        if not has_data:
            return empty
        return {
            "has_data": True,
            "total": total,
            "case_driven_rate": round(
                sum(_safe_float(c, "_case_driven_rate") for c in intents)
                / max(total, 1),
                1,
            ),
            "strictness_score": round(
                sum(_safe_float(c, "_strictness_score") for c in intents)
                / max(total, 1),
                1,
            ),
            "case_completeness": round(
                sum(_safe_float(c, "_case_completeness") for c in intents)
                / max(total, 1),
                1,
            ),
            "evidence_total": round(
                sum(_safe_float(c, "_evidence_total") for c in intents) / max(total, 1),
                1,
            ),
        }

    def get_intents_by_ids(
        self, case_ids: list[str], include_virtual_inherited: bool = True
    ) -> list[dict[str, Any]]:
        if not case_ids:
            return []
        wanted = [x for cid in case_ids if (x := str(cid).strip())]
        if not wanted:
            return []
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
                    placeholders = ",".join(["%s"] * len(wanted))
                    rows = c.execute(
                        f"""
                        SELECT tc.*,
                               cm.confidence AS _confidence,
                               cm.risk_level AS _risk_level,
                               cm.review_status AS _review_status,
                               cm.case_coverage AS _case_coverage,
                               cm.case_completeness AS _case_completeness,
                               cm.evidence_total AS _evidence_total,
                               cm.avg_confidence AS _avg_confidence,
                               cm.case_driven_rate AS _case_driven_rate,
                               cm.strictness_score AS _strictness_score,
                               cm.execution_factors AS _execution_factors,
                               cm.evidence_confidence AS _evidence_confidence,
                               cm.evidence_ai_raw AS _evidence_ai_raw,
                               cm.evidence_cal_accuracy AS _evidence_cal_accuracy,
                               cm.evidence_strength AS _evidence_strength,
                               cm.evidence_level AS _evidence_level,
                               cm.evidence_verdict AS _evidence_verdict,
                               cm.evidence_cold_start AS _evidence_cold_start
                        FROM test_cases tc
                        LEFT JOIN case_meta cm
                          ON tc.case_id = cm.case_id AND tc.project = cm.project AND tc.version_id = cm.version_id
                        WHERE tc.project=%s AND tc.version_id=%s AND tc.case_id IN ({placeholders}) AND tc.valid_status <> 'stale'
                        ORDER BY tc.case_id
                        """,
                        [project, active_version_id] + wanted,
                    ).fetchall()
                    inherited_rows = []
                    if include_virtual_inherited:
                        inherited_rows = self._load_virtual_inherited_rows(
                            c, project, active_version_id, case_ids=wanted
                        )
                finally:
                    if c:
                        with guard("get_intents_by_ids: c.close()", log=_log, level=logging.WARNING):
                            c.close()
            self._last_error = ""
            intent_map = {}
            for row in (rows or []) + (inherited_rows or []):
                item = dict(row)
                try:
                    item["payload"] = json.loads(item.get("payload_json") or "{}")
                except Exception:
                    _log.warning(
                        "[Store] payload_json 解析失败, case_id=%s", item.get("case_id")
                    )
                    item["payload"] = {}
                intent = _row_to_intent(item)
                for key in (
                    "_confidence",
                    "_risk_level",
                    "_review_status",
                    "_case_coverage",
                    "_case_completeness",
                    "_evidence_total",
                    "_avg_confidence",
                    "_case_driven_rate",
                    "_strictness_score",
                    "_execution_factors",
                    "_evidence_confidence",
                    "_evidence_ai_raw",
                    "_evidence_cal_accuracy",
                    "_evidence_strength",
                    "_evidence_level",
                    "_evidence_verdict",
                    "_evidence_cold_start",
                ):
                    if item.get(key) not in (None, ""):
                        intent[key] = item.get(key)
                intent_map[str(intent.get("case_id", "")).strip()] = intent
            ordered = [intent_map[cid] for cid in wanted if cid in intent_map]
            return self._attach_execution_status(ordered, project, active_version_id)
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] get_intents_by_ids 查询失败: %s", e)
            return []

    def get_intents_by_files(
        self, case_asset_files: list[str], include_virtual_inherited: bool = True
    ) -> list[dict[str, Any]]:
        if not case_asset_files:
            return []
        names = [str(Path(str(f)).name) for f in case_asset_files if str(f).strip()]
        if not names:
            return []
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
                    placeholders = ",".join(["%s"] * len(names))
                    rows = c.execute(
                        f"""
                        SELECT tc.*,
                               cm.confidence AS _confidence,
                               cm.risk_level AS _risk_level,
                               cm.review_status AS _review_status,
                               cm.case_coverage AS _case_coverage,
                               cm.case_completeness AS _case_completeness,
                               cm.evidence_total AS _evidence_total,
                               cm.avg_confidence AS _avg_confidence,
                               cm.case_driven_rate AS _case_driven_rate,
                               cm.strictness_score AS _strictness_score,
                               cm.execution_factors AS _execution_factors,
                               cm.evidence_confidence AS _evidence_confidence,
                               cm.evidence_ai_raw AS _evidence_ai_raw,
                               cm.evidence_cal_accuracy AS _evidence_cal_accuracy,
                               cm.evidence_strength AS _evidence_strength,
                               cm.evidence_level AS _evidence_level,
                               cm.evidence_verdict AS _evidence_verdict,
                               cm.evidence_cold_start AS _evidence_cold_start
                        FROM test_cases tc
                        LEFT JOIN case_meta cm
                          ON tc.case_id = cm.case_id AND tc.project = cm.project AND tc.version_id = cm.version_id
                        WHERE tc.project=%s AND tc.version_id=%s AND tc.case_asset_file IN ({placeholders})
                        ORDER BY tc.case_id
                        """,
                        [project, active_version_id] + names,
                    ).fetchall()
                    inherited_rows = []
                    if include_virtual_inherited:
                        inherited_rows = self._load_virtual_inherited_rows(
                            c, project, active_version_id, case_asset_files=names
                        )
                finally:
                    if c:
                        with guard("get_intents_by_files: c.close()", log=_log, level=logging.WARNING):
                            c.close()
            self._last_error = ""
            result = []
            for row in (rows or []) + (inherited_rows or []):
                item = dict(row)
                try:
                    item["payload"] = json.loads(item.get("payload_json") or "{}")
                except Exception:
                    _log.warning(
                        "[Store] payload_json 解析失败, case_id=%s", item.get("case_id")
                    )
                    item["payload"] = {}
                intent = _row_to_intent(item)
                for key in (
                    "_confidence",
                    "_risk_level",
                    "_review_status",
                    "_case_coverage",
                    "_case_completeness",
                    "_evidence_total",
                    "_avg_confidence",
                    "_case_driven_rate",
                    "_strictness_score",
                    "_execution_factors",
                    "_evidence_confidence",
                    "_evidence_ai_raw",
                    "_evidence_cal_accuracy",
                    "_evidence_strength",
                    "_evidence_level",
                    "_evidence_verdict",
                    "_evidence_cold_start",
                ):
                    if item.get(key) not in (None, ""):
                        intent[key] = item.get(key)
                result.append(intent)
            return self._attach_execution_status(result, project, active_version_id)
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] get_intents_by_files 查询失败: %s", e)
            return []
