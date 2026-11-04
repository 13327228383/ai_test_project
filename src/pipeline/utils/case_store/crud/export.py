# -*- coding: utf-8 -*-
"""
CaseStore 导出 mixin

职责：
- export_to_excel / health_stats
"""

import json
from src.core.guarded import guard
import logging
from datetime import datetime
from typing import Any

from .._constants import CASE_EXPORT_COLUMNS

_log = logging.getLogger(__name__)


class _VisExportMixin:
    """测试用例导出操作。"""

    def _build_intents_workbook(self, include_virtual_inherited: bool = True):
        """构建测试用例 Excel workbook（供 export_to_excel / export_to_excel_bytes 复用）。"""
        from openpyxl import Workbook

        intents = self.list_intents_with_meta(
            include_virtual_inherited=include_virtual_inherited
        )
        wb = Workbook()
        ws = wb.active
        ws.title = "Test Cases"
        # v3.0: 表头用显示名
        ws.append([display_name for _, display_name, _ in CASE_EXPORT_COLUMNS])

        # ── ★ 按列名分派导出 handler ──
        def _export_case_type(intent):
            return intent.get("case_type", intent.get("type", ""))

        def _export_risk_level(intent):
            return intent.get("risk_level", intent.get("risk", ""))

        def _export_precondition(intent):
            return intent.get("precondition", "")

        def _export_test_steps(intent):
            steps = intent.get("test_steps") or ""
            if isinstance(steps, str):
                return steps
            return json.dumps(steps, ensure_ascii=False, indent=2)

        def _export_expected_result(intent):
            return intent.get("expected_result", "")

        def _export_intent_summary(intent):
            return intent.get("intent_summary", "")

        def _export_checks(intent):
            checks = intent.get("checks") or []
            if isinstance(checks, str):
                try:
                    checks = json.loads(checks)
                except Exception as _e:
                    _log.debug(
                        "[export] _build_intents_workbook 异常: %s", _e, exc_info=True
                    )
                    checks = []
            return json.dumps(checks, ensure_ascii=False, indent=2)

        def _export_actions(intent):
            actions = intent.get("actions") or []
            if isinstance(actions, str):
                try:
                    actions = json.loads(actions)
                except Exception as _e:
                    _log.debug("[export] 非关键异常: %s", _e, exc_info=True)
                    actions = []
            return json.dumps(actions, ensure_ascii=False, indent=2)

        def _export_payload_json(intent):
            return json.dumps(intent.get("payload", ""), ensure_ascii=False, indent=2)

        def _export_verification_status(intent):
            return intent.get("verification_status", "")

        def _export_verification_time(intent):
            return str(intent.get("verification_time", ""))

        def _export_failure_reason(intent):
            return intent.get("_failure_reason", "")

        def _export_screenshot(intent):
            return intent.get("_screenshot", "")

        _COL_HANDLERS = {
            "case_type": _export_case_type,
            "type": _export_case_type,
            "risk_level": _export_risk_level,
            "risk": _export_risk_level,
            "precondition": _export_precondition,
            "test_steps": _export_test_steps,
            "expected_result": _export_expected_result,
            "intent_summary": _export_intent_summary,
            "checks": _export_checks,
            "actions": _export_actions,
            "payload_json": _export_payload_json,
            "verification_status": _export_verification_status,
            "verification_time": _export_verification_time,
            "_failure_reason": _export_failure_reason,
            "_screenshot": _export_screenshot,
        }

        for intent in intents:
            row = []
            for col_name, _display_name, _width in CASE_EXPORT_COLUMNS:
                handler = _COL_HANDLERS.get(col_name)
                if handler:
                    row.append(handler(intent))
                else:
                    row.append(intent.get(col_name, ""))
            ws.append(row)
        return wb

    def export_to_excel(
        self, filepath: str, *, include_virtual_inherited: bool = True
    ) -> bool:
        try:
            from openpyxl import Workbook  # noqa: F401 — 防御性检查依赖
        except ImportError:
            _log.error("[Store] openpyxl 未安装，无法导出 Excel")
            self._last_error = "openpyxl not installed"
            return False
        try:
            wb = self._build_intents_workbook(
                include_virtual_inherited=include_virtual_inherited
            )
            wb.save(filepath)
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] export_to_excel 失败: %s", e)
            return False

    def export_to_excel_bytes(self, *, include_virtual_inherited: bool = True) -> bytes:
        """构建测试用例 Excel bytes（Web 路由流式下载用，不落盘）。"""
        wb = self._build_intents_workbook(
            include_virtual_inherited=include_virtual_inherited
        )
        try:
            import io

            bio = io.BytesIO()
            wb.save(bio)
            return bio.getvalue()
        finally:
            try:
                wb.close()
            except Exception as _e:
                _log.debug("[export] export_to_excel_bytes 异常: %s", _e, exc_info=True)

    def health_stats(self) -> dict[str, Any]:
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
                        "SELECT COUNT(*) AS total FROM test_cases WHERE project=%s AND version_id=%s",
                        (project, active_version_id),
                    ).fetchone()
                    total = int(row["total"]) if row else 0
                    row = c.execute(
                        """
                        SELECT COUNT(DISTINCT tc.case_id) AS executed
                        FROM test_cases tc
                        INNER JOIN (
                            SELECT case_id, MAX(executed_at) AS max_at
                            FROM executions WHERE project=%s AND version_id=%s
                            GROUP BY case_id
                        ) latest ON tc.case_id=latest.case_id
                        INNER JOIN executions e
                          ON e.case_id=latest.case_id AND e.executed_at=latest.max_at
                        WHERE tc.project=%s AND tc.version_id=%s AND e.status IN ('passed','success','verified')
                        """,
                        (project, active_version_id, project, active_version_id),
                    ).fetchone()
                    executed = int(row["executed"]) if row else 0
                finally:
                    if c:
                        with guard("health_stats: c.close()", log=_log, level=logging.WARNING):
                            c.close()
            self._last_error = ""
            return {
                "total_cases": total,
                "executed": executed,
                "pending": total - executed,
                "coverage": round(executed / total, 2) if total else 0.0,
                "updated_at": datetime.now().isoformat(),
            }
        except Exception as e:
            self._last_error = str(e)
            _log.error("[Store] health_stats 查询失败: %s", e)
            return {
                "total_cases": 0,
                "executed": 0,
                "pending": 0,
                "coverage": 0.0,
                "updated_at": datetime.now().isoformat(),
            }
