# -*- coding: utf-8 -*-
"""冒烟用例 Excel 文件构建（openpyxl 渲染） — 从 smoke_generator.py 拆出，零逻辑变更。"""

import io
import logging
from datetime import datetime
from typing import Any

_log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════
# Excel 列定义（对齐 测试用例示例.md）
# ══════════════════════════════════════════════════════════
EXCEL_HEADERS = [
    "目录层级",
    "目录名称",
    "用例ID",
    "用例标题",
    "预置条件",
    "用例步骤",
    "预期结果",
    "用例类型",
    "用例等级",
    "自动化标记",
    "门槛标记",
    "用例作者",
    "创建时间",
    "用例执行结果",
    "执行时间",
]


def _sanitize_sheet_name(name: str) -> str:
    """清理 Excel sheet 名称（最多31字符，去除非法字符）。"""
    import re

    name = re.sub(r"[\\/*?:\[\]]", "", str(name))
    return name[:31]


def _build_smoke_excel(
    modules_order: list[str],
    module_cases: dict[str, list[dict[str, Any]]],
    project_name: str,
    author: str = "周文波",
) -> bytes:
    """
    构建冒烟用例 Excel 文件。
    层级结构：
      .  → 一级模块（目录行）
      .. → 二级模块（目录行）
      ... → 用例行
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    except ImportError:
        _log.error("缺少 openpyxl 依赖，请运行: pip install openpyxl")
        return b""

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "冒烟用例"

    # 样式
    header_fill = PatternFill(
        start_color="1A2D5A", end_color="1A2D5A", fill_type="solid"
    )
    header_font = Font(color="FFFFFF", bold=True, size=11, name="微软雅黑")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    dir_fill = PatternFill(start_color="E8ECF0", end_color="E8ECF0", fill_type="solid")
    dir_font = Font(bold=True, size=11, name="微软雅黑", color="374151")
    cell_font = Font(size=10, name="微软雅黑")
    cell_align = Alignment(vertical="center", wrap_text=True)
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin", color="D1D5DB"),
        right=Side(style="thin", color="D1D5DB"),
        top=Side(style="thin", color="D1D5DB"),
        bottom=Side(style="thin", color="D1D5DB"),
    )

    # 写表头
    for ci, h in enumerate(EXCEL_HEADERS, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_align
        cell.border = thin_border

    row_num = 2
    case_idx = 0
    create_time = datetime.now().strftime("%Y-%m-%d")

    for mod_name in modules_order:
        cases = module_cases.get(mod_name, [])

        # 一级模块目录行
        ws.cell(row=row_num, column=1, value=".").font = dir_font
        ws.cell(row=row_num, column=2, value=mod_name).font = dir_font
        for ci in range(1, len(EXCEL_HEADERS) + 1):
            ws.cell(row=row_num, column=ci).fill = dir_fill
            ws.cell(row=row_num, column=ci).border = thin_border
        row_num += 1

        if not cases:
            continue

        # 二级目录行（用例所属模块）
        ws.cell(row=row_num, column=1, value="..").font = dir_font
        ws.cell(row=row_num, column=2, value=mod_name).font = dir_font
        for ci in range(1, len(EXCEL_HEADERS) + 1):
            ws.cell(row=row_num, column=ci).fill = dir_fill
            ws.cell(row=row_num, column=ci).border = thin_border
        row_num += 1

        # 用例行
        for case in cases:
            case_idx += 1
            case_id = "Test_Smoke_%s_%03d" % (project_name.replace(" ", "_"), case_idx)

            ws.cell(row=row_num, column=1, value="...").font = cell_font
            ws.cell(row=row_num, column=2, value="").font = cell_font
            ws.cell(row=row_num, column=3, value=case_id).font = cell_font
            ws.cell(row=row_num, column=4, value=case.get("title", "")).font = cell_font
            ws.cell(row=row_num, column=5, value=case.get("precondition", "")).font = (
                cell_font
            )
            ws.cell(row=row_num, column=6, value=case.get("steps", "")).font = cell_font
            ws.cell(row=row_num, column=7, value=case.get("expected", "")).font = (
                cell_font
            )
            ws.cell(
                row=row_num, column=8, value=case.get("case_type", "功能测试")
            ).font = cell_font
            ws.cell(row=row_num, column=9, value=case.get("risk_level", "高")).font = (
                cell_font
            )
            ws.cell(row=row_num, column=10, value="否").font = cell_font
            ws.cell(
                row=row_num, column=11, value=case.get("threshold_flag", "是")
            ).font = cell_font
            ws.cell(row=row_num, column=12, value=author).font = cell_font
            ws.cell(row=row_num, column=13, value=create_time).font = cell_font
            ws.cell(row=row_num, column=14, value="").font = cell_font
            ws.cell(row=row_num, column=15, value="").font = cell_font

            for ci in range(1, len(EXCEL_HEADERS) + 1):
                cell = ws.cell(row=row_num, column=ci)
                cell.border = thin_border
                if ci in (1, 3, 8, 9, 10, 11, 14, 15):
                    cell.alignment = center_align
                else:
                    cell.alignment = cell_align

            row_num += 1

    # 列宽
    col_widths = {
        1: 8,
        2: 18,
        3: 28,
        4: 22,
        5: 30,
        6: 45,
        7: 40,
        8: 10,
        9: 8,
        10: 10,
        11: 8,
        12: 10,
        13: 12,
        14: 12,
        15: 12,
    }
    for ci, w in col_widths.items():
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w

    # 冻结首行
    ws.freeze_panes = "A2"

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio.getvalue()
