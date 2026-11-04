# -*- coding: utf-8 -*-
from __future__ import annotations

"""
CaseStore 基础 mixin

职责：
- __init__ / last_error / _get_db
- _ensure_tables（DDL 表初始化）
- _load_virtual_inherited_rows（虚拟继承读取）
- _attach_execution_status（执行状态补齐）
"""

import logging
import threading
from pathlib import Path
from typing import Any

from .._constants import _case_tables_ensured

_log = logging.getLogger(__name__)


class _VisBaseMixin:
    """CaseStore 基础能力。"""

    def __init__(self, db_getter=None):
        self._last_error = ""
        self._db_getter = db_getter
        self._lock = threading.RLock()

    def last_error(self) -> str:
        return self._last_error

    def _get_db(self):
        if self._db_getter is not None:
            return self._db_getter()
        from src.core.db_manager import get_connection

        return get_connection()

    @staticmethod
    def _ensure_tables(c) -> None:
        """建表已统一由 src/core/schema.sql 负责，此处仅作占位以保持调用兼容。
        ★ 2026-06-22 改造：schema.sql 是 test_cases / case_meta 的唯一事实源。
        - 删除原 CREATE TABLE test_cases / case_meta
        - 删除原 ALTER TABLE 兼容补列逻辑（schema.sql 已包含全部列与索引）
        本函数保留签名以兼容调用方 `self._ensure_tables(c)`。
        """
        global _case_tables_ensured
        if _case_tables_ensured:
            return
        _case_tables_ensured = True
        return

    def _load_virtual_inherited_rows(
        self,
        c,
        project: str,
        active_version_id: int,
        *,
        case_asset_file: str | None = None,
        case_ids: list[str] | None = None,
        case_asset_files: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        filters = [
            "r.project=%s",
            "r.version_id=%s",
            "r.change_type='unchanged'",
            "r.origin_version_id > 0",
            "tc.valid_status NOT IN ('retired', 'stale')",
            "NOT EXISTS ("
            "SELECT 1 FROM test_cases cur "
            "WHERE cur.project=r.project AND cur.version_id=r.version_id AND cur.case_id=tc.case_id"
            ")",
        ]
        params: list[Any] = [project, active_version_id]
        if case_asset_file:
            filters.append("tc.case_asset_file=%s")
            params.append(str(case_asset_file))
        if case_ids:
            wanted = [x for cid in case_ids if (x := str(cid).strip())]
            if wanted:
                placeholders = ",".join(["%s"] * len(wanted))
                filters.append(f"tc.case_id IN ({placeholders})")
                params.extend(wanted)
        if case_asset_files:
            names = [str(Path(str(f)).name) for f in case_asset_files if str(f).strip()]
            if names:
                placeholders = ",".join(["%s"] * len(names))
                filters.append(f"tc.case_asset_file IN ({placeholders})")
                params.extend(names)
        rows = c.execute(
            """
            SELECT tc.*,
                   r.req_id AS _active_req_id,
                   tc.version_id AS _source_version_id,
                   tc.case_id AS _source_case_id
            FROM requirements r
            JOIN test_cases tc
              ON tc.project = r.project
             AND tc.version_id = r.origin_version_id
             AND tc.req_id = r.origin_req_id
            WHERE """
            + " AND ".join(filters)
            + """
            ORDER BY tc.case_id
            """,
            tuple(params),
        ).fetchall()
        result = []
        for row in rows or []:
            item = dict(row)
            item["origin_version_id"] = (
                item.get("_source_version_id") or item.get("version_id") or 0
            )
            item["origin_case_id"] = (
                item.get("_source_case_id") or item.get("case_id") or ""
            )
            item["version_id"] = active_version_id
            item["req_id"] = item.get("_active_req_id") or item.get("req_id") or ""
            item["case_source"] = "inherited_virtual"
            # ★ 虚拟继承行 = 从旧版本继承的 change_type='unchanged' 用例，
            #   在旧版本已审核通过 → 自动视为 approved，避免审核闸门确认率永远不足 90%
            #   （case_meta 按 version_id 隔离，虚拟行在当前版本 case_meta 中无记录，
            #    若不标记 approved 会导致 _case_review_gate_status 误判为 pending）
            item["_review_status"] = "approved"
            result.append(item)
        return result

    def _attach_execution_status(
        self, intents: list[dict[str, Any]], project: str, version_id: int
    ) -> list[dict[str, Any]]:
        if not intents:
            return intents
        case_ids = sorted(
            {x for c in intents if (x := str(c.get("case_id", "") or "").strip())}
        )
        if not case_ids:
            return intents
        status_map: dict[str, dict[str, Any]] = {}
        c = None
        try:
            c = self._get_db()
            placeholders = ",".join(["%s"] * len(case_ids))
            rows = c.execute(
                f"""
                SELECT e.case_id, e.status, e.executed_at, e.failure_reason,
                       e.failure_category, e.screenshot
                FROM executions e
                INNER JOIN (
                    SELECT case_id, MAX(executed_at) AS max_at
                    FROM executions
                    WHERE project=%s AND version_id=%s AND case_id IN ({placeholders})
                    GROUP BY case_id
                ) latest
                  ON e.case_id = latest.case_id AND e.executed_at = latest.max_at
                WHERE e.project=%s AND e.version_id=%s
                """,
                tuple([project, version_id] + case_ids + [project, version_id]),
            ).fetchall()
            for row in rows or []:
                rid = str(row.get("case_id", "") or "").strip()
                if rid:
                    status_map[rid] = dict(row)
        except Exception as e:
            _log.warning("[Store] 执行状态补齐失败: %s", str(e)[:120])
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[base] 非关键异常: %s", _e, exc_info=True)
        for intent in intents:
            cid = str(intent.get("case_id", "") or "").strip()
            status = status_map.get(cid)
            if status:
                intent["verification_status"] = (
                    str(status.get("status", "") or "").strip().upper()
                )
                intent["verification_time"] = status.get("executed_at")
                intent["_failure_reason"] = status.get("failure_reason", "")
                intent["_screenshot"] = status.get("screenshot", "")
                # ★ v3.0.1 (2026-07-10): 补齐 failure_category，供 _manual_retest 风险分级使用
                intent["failure_category"] = str(
                    status.get("failure_category", "") or ""
                ).strip()
        return intents
