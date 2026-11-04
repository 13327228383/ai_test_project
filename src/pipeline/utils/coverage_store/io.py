# -*- coding: utf-8 -*-
"""
覆盖率 MySQL 事实源存储 - 导入导出 Mixin
"""
from pathlib import Path
from typing import Any

from .base import _ALL_DIMS
import logging

_log = logging.getLogger(__name__)


class _CoverageIOMixin:
    """覆盖率导入导出 Mixin。"""

    def _build_coverage_workbook(self):
        """构建覆盖矩阵 Excel workbook（供 export_to_excel / export_to_excel_bytes 复用）。
        返回 (wb, count)。无数据时抛 ValueError。"""
        coverage = self.load_coverage()
        if not coverage or not coverage.get("matrix"):
            raise ValueError("MySQL 中没有可导出的覆盖数据")
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "覆盖矩阵"
        headers = [
            "需求编号",
            "需求描述",
            "正向覆盖",
            "异常覆盖",
            "边界值",
            "安全",
            "API校验",
            "API覆盖",
            "DB覆盖",
            "页面覆盖",
            "追溯链路",
            "测试用例数",
        ]
        header_font = Font(name="微软雅黑", bold=True, size=11, color="FFFFFF")
        header_fill = PatternFill(
            start_color="4472C4", end_color="4472C4", fill_type="solid"
        )
        green_fill = PatternFill(
            start_color="C6EFCE", end_color="C6EFCE", fill_type="solid"
        )
        yellow_fill = PatternFill(
            start_color="FFEB9C", end_color="FFEB9C", fill_type="solid"
        )
        gray_fill = PatternFill(
            start_color="D9D9D9", end_color="D9D9D9", fill_type="solid"
        )

        for ci, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=ci, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        matrix = coverage.get("matrix") or {}
        gaps = coverage.get("gaps") or []
        for ri, (rid, dims) in enumerate(matrix.items(), 2):
            ws.cell(row=ri, column=1, value=rid)
            desc = ""
            for g in gaps:
                if g.get("req_id") == rid:
                    desc = str(g.get("description", "") or "")
                    break
            ws.cell(row=ri, column=2, value=desc[:80])
            for ci, d in enumerate(_ALL_DIMS, 3):
                s = str(dims.get(d, "missing") or "missing")
                cell = ws.cell(row=ri, column=ci)
                if s == "covered":
                    cell.value = "✓"
                    cell.fill = green_fill
                elif s == "n/a":
                    cell.value = "—"
                    cell.fill = gray_fill
                else:
                    cell.value = "⚠"
                    cell.fill = yellow_fill
                cell.alignment = Alignment(horizontal="center")
            intent_count = 0
            for g in gaps:
                if g.get("req_id") == rid:
                    intent_count = int(g.get("total_cases", 0) or 0)
                    break
            ws.cell(row=ri, column=len(headers), value=intent_count)

        ws.auto_filter.ref = "A1:%s%d" % (
            chr(ord("A") + len(headers) - 1),
            len(matrix) + 1,
        )
        ws.freeze_panes = "A2"
        return wb, len(matrix)

    def export_to_excel(self, save_path: str) -> dict[str, Any]:
        """从 MySQL 导出覆盖矩阵为 Excel 文件（显式导出，CLI/落盘场景）。"""
        result = {"success": False, "path": str(save_path), "count": 0, "error": ""}
        try:
            wb, count = self._build_coverage_workbook()
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
            return result

    def export_to_excel_bytes(self) -> bytes:
        """从 MySQL 导出覆盖矩阵为 Excel bytes（Web 路由流式下载用，不落盘）。"""
        wb, _ = self._build_coverage_workbook()
        try:
            import io

            bio = io.BytesIO()
            wb.save(bio)
            return bio.getvalue()
        finally:
            try:
                wb.close()
            except Exception as _e:
                _log.debug("[io] export_to_excel_bytes 异常: %s", _e, exc_info=True)

    def import_from_excel(self, excel_path: str) -> dict[str, Any]:
        """从 Excel 回填到 MySQL（显式导入，不参与主链路）。"""
        result = {"success": False, "path": str(excel_path), "count": 0, "error": ""}
        try:
            import openpyxl

            wb = openpyxl.load_workbook(excel_path, data_only=True)
            ws = wb.active
            matrix: dict[str, dict[str, str]] = {}
            gaps: list[dict[str, Any]] = []
            for row_idx in range(2, ws.max_row + 1):
                rid = str(ws.cell(row=row_idx, column=1).value or "").strip()
                if not rid:
                    continue
                col_map = {
                    "positive": 3,
                    "negative": 4,
                    "boundary": 5,
                    "security": 6,
                    "api_check": 7,
                    "api_coverage": 8,
                    "db_coverage": 9,
                    "page_coverage": 10,
                    "trace_complete": 11,
                }
                dims = {}
                gap_list = []
                for d, col in col_map.items():
                    v = str(ws.cell(row=row_idx, column=col).value or "")
                    if "✓" in v or v.lower() == "covered":
                        dims[d] = "covered"
                    elif "—" in v or "n/a" in v.lower():
                        dims[d] = "n/a"
                    else:
                        dims[d] = "missing"
                        gap_list.append(d)
                matrix[rid] = dims
                if gap_list:
                    gaps.append(
                        {
                            "req_id": rid,
                            "description": str(
                                ws.cell(row=row_idx, column=2).value or ""
                            )[:80],
                            "missing_dimensions": gap_list,
                            "total_cases": int(
                                ws.cell(row=row_idx, column=12).value or 0
                            ),
                        }
                    )
            wb.close()
            result["count"] = len(matrix)
            if not matrix:
                result["error"] = "Excel 中没有可导入的覆盖数据"
                return result
            cov = {
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
                "total_cases": sum(int(g.get("total_cases", 0) or 0) for g in gaps),
            }
            if self.upsert_coverage(cov):
                result["success"] = True
            else:
                result["error"] = self._last_error or "upsert_coverage 失败"
            return result
        except Exception as e:
            result["error"] = str(e)
            self._last_error = str(e)
            return result
