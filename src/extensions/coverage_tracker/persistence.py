# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""覆盖矩阵持久化（MySQL + Excel 导入/导出） — 从 coverage_tracker.py 拆出，零逻辑变更。"""

import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


class _PersistenceMixin:
    """save_matrix + import_from_excel + export_to_excel + load_from_db。"""

    # ═══════════════════════════════════════════════
    # 持久化（事实源：MySQL coverage_results；Excel 仅作显式导出附件）
    # ═══════════════════════════════════════════════

    def save_matrix(self, coverage_result: dict) -> Path | None:
        """将覆盖矩阵写入 MySQL（事实源）。

        说明：
            - 唯一事实源：MySQL ``coverage_results`` / ``coverage_summaries``
            - Excel 导出仅通过手动调用 ``coverage_store.export_to_excel()`` 触发，
              不再由 save_matrix 自动生成
        """
        # 写入 MySQL（事实源）
        with guard("save_matrix: from src.pipeline.utils.coverage_sto", log=_log, level=logging.INFO):
            from src.pipeline.utils.coverage_store import coverage_store

            ok = coverage_store.upsert_coverage(coverage_result)
            if not ok:
                _log.error(
                    "  [CoverageTracker] MySQL coverage_results 写入失败: %s"
                    % (coverage_store.last_error or "未知原因")
                )
        _log.info("[CoverageTracker] 覆盖矩阵已保存 (MySQL)")
        return None

    # -----------------------------------------------------------------
    # 显式导入/导出接口（让 coverage_matrix.xlsx 仅作导入源/导出物）
    # -----------------------------------------------------------------
    def import_from_excel(self, excel_path: str) -> dict[str, Any]:
        """显式从 Excel 回填到 MySQL（不作为运行时兜底）。"""
        try:
            from src.pipeline.utils.coverage_store import coverage_store

            return coverage_store.import_from_excel(excel_path)
        except Exception as e:
            return {
                "success": False,
                "path": str(excel_path),
                "count": 0,
                "error": "导入失败: %s" % str(e)[:120],
            }

    def export_to_excel(self, save_path: str) -> dict[str, Any]:
        """显式从 MySQL 导出 Excel（不作为运行时事实源）。"""
        try:
            from src.pipeline.utils.coverage_store import coverage_store

            return coverage_store.export_to_excel(save_path)
        except Exception as e:
            return {
                "success": False,
                "path": str(save_path),
                "count": 0,
                "error": "导出失败: %s" % str(e)[:120],
            }

    def export_to_excel_bytes(self) -> bytes:
        """从 MySQL 导出覆盖矩阵为 Excel bytes（Web 路由流式下载用，不落盘）。"""
        from src.pipeline.utils.coverage_store import coverage_store

        return coverage_store.export_to_excel_bytes()

    def load_from_db(self) -> dict[str, Any]:
        """主链路从 MySQL 读覆盖矩阵（与 compute_coverage() 返回同构）。"""
        try:
            from src.pipeline.utils.coverage_store import coverage_store

            return coverage_store.load_coverage()
        except Exception as e:
            _log.info("[CoverageTracker] MySQL 读取失败: %s", str(e)[:120])
            return {}
