# -*- coding: utf-8 -*-
"""Excel 导出 — 从 requirement_store.py 拆出，零逻辑变更。"""

import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

REQ_EXPORT_COLUMNS = [
    ("req_id", "需求编号", 12),
    ("module", "功能模块", 20),
    ("description", "需求描述", 60),
    ("is_generated", "是否已生成测试用例", 16),
    ("dependency_req_id", "依赖需求编号", 16),
    ("review_status", "审核状态", 14),
]


class _ExcelMixin:
    """export_to_excel — 把 requirements 镜像导出为 Excel。"""

    def _build_requirements_workbook(self):
        """构建需求拆解 Excel workbook（供 export_to_excel / export_to_excel_bytes 复用）。"""
        reqs = self.list_requirements()
        import openpyxl
        from src.core.excel_utils import _create_sheet

        wb = openpyxl.Workbook()
        if "Sheet" in wb.sheetnames:
            del wb["Sheet"]
        _create_sheet(wb, "需求拆解", REQ_EXPORT_COLUMNS, reqs)
        return wb, len(reqs)

    def export_to_excel(self, save_path: str) -> dict[str, Any]:
        """把当前项目 requirements 镜像导出为需求拆解 Excel 文件（CLI/落盘场景）。"""
        reqs = self.list_requirements()
        result = {
            "success": False,
            "path": str(save_path),
            "count": len(reqs),
            "error": "",
        }
        try:
            wb, count = self._build_requirements_workbook()
            target = Path(save_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            wb.save(str(target))
            wb.close()
            result["success"] = True
            result["count"] = count
            return result
        except Exception as e:
            result["error"] = str(e)
            self._last_error = str(e)
            _log.info(
                "  [RequirementStore] requirements 导出 Excel 失败: %s", str(e)[:120]
            )
            return result

    def export_to_excel_bytes(self) -> bytes:
        """把当前项目 requirements 镜像导出为 Excel bytes（Web 路由流式下载用，不落盘）。"""
        wb, _ = self._build_requirements_workbook()
        try:
            import io

            bio = io.BytesIO()
            wb.save(bio)
            return bio.getvalue()
        finally:
            try:
                wb.close()
            except Exception as _e:
                _log.debug(
                    "[excel_io] export_to_excel_bytes 异常: %s", _e, exc_info=True
                )
