# -*- coding: utf-8 -*-
"""
Excel 通用 Sheet I/O 工具函数
==============================
从 ExcelHandler 类中提取的纯静态方法，不依赖类实例。
用于 _create_sheet / _write_data_rows / _apply_status_formatting / _read_sheet。
"""

import json, re
from datetime import date, datetime
import logging

_log = logging.getLogger(__name__)

from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


def _create_sheet(wb, sheet_name, columns, data):
    """创建格式化工作表（表头蓝色填充、数据区自动列宽、冻结首行、自动筛选）。"""
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(title=sheet_name)
    header_font = Font(name="微软雅黑", bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(
        start_color="4472C4", end_color="4472C4", fill_type="solid"
    )
    header_alignment = Alignment(horizontal="center", vertical="center")
    data_alignment = Alignment(vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    for col_idx, (_, col_name, col_width) in enumerate(columns, 1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border
        ws.column_dimensions[get_column_letter(col_idx)].width = col_width
    _write_data_rows(ws, data, 2, columns)
    ws.freeze_panes = "A2"
    _apply_status_formatting(ws, columns, 2, len(data) + 1)
    ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(columns)), len(data) + 1)
    return wb


def _write_data_rows(ws, data, start_row, columns):
    """将 dict 列表写入工作表数据行。"""
    data_alignment = Alignment(vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    for row_idx, row_data in enumerate(data, start_row):
        for col_idx, (attr_name, _, _) in enumerate(columns, 1):
            value = row_data.get(attr_name, "")
            if attr_name in ("created_date", "verification_time"):
                if isinstance(value, (date, datetime)):
                    value = value.strftime("%Y-%m-%d %H:%M:%S")
                elif not value:
                    value = (
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        if attr_name == "created_date"
                        else ""
                    )
            elif attr_name == "is_generated":
                if isinstance(value, bool):
                    pass
                elif isinstance(value, str):
                    value = value.strip().lower() in ("true", "1", "yes")
                elif isinstance(value, (int, float)):
                    value = bool(value) and value != 0
                else:
                    value = False
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = data_alignment
            cell.border = thin_border
    return len(data)


def _apply_status_formatting(ws, columns, start_row, end_row):
    """verification_status 列的条件着色（PASS=绿色，FAIL=红色）。"""
    status_col_idx = None
    for ci, (attr_name, _, _) in enumerate(columns, 1):
        if attr_name == "verification_status":
            status_col_idx = ci
            break
    if not status_col_idx:
        return
    pass_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    pass_font = Font(name="微软雅黑", bold=True, color="006100")
    fail_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    fail_font = Font(name="微软雅黑", bold=True, color="9C0006")
    for row_idx in range(start_row, end_row + 1):
        cell = ws.cell(row=row_idx, column=status_col_idx)
        val = str(cell.value or "").strip().upper()
        if val == "PASS":
            cell.fill = pass_fill
            cell.font = pass_font
        elif val == "FAIL":
            cell.fill = fail_fill
            cell.font = fail_font


def _read_sheet(wb, sheet_name, columns):
    """读取工作表为 dict 列表。特殊处理 case_actions_json 格式（JSON信封/人机双读/纯文本）。"""
    if sheet_name not in wb.sheetnames:
        return []
    ws = wb[sheet_name]
    data = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if all(v is None for v in row):
            continue
        row_dict = {}
        for col_idx, (attr_name, _, _) in enumerate(columns):
            value = row[col_idx] if col_idx < len(row) else None
            if attr_name == "case_actions_json" and isinstance(value, str):
                # 兼容两种格式：
                #   旧格式 — JSON 信封 {"text": "...", "checks": {...}}
                #   新格式 — 人机双读：步骤文本\n\n[step_checks]\n{checks JSON}
                if value.strip().startswith('{"text":') or value.strip().startswith(
                    '{"steps":'
                ):
                    try:
                        parsed = json.loads(value)
                        if isinstance(parsed, dict):
                            row_dict["_step_checks"] = parsed.get("checks", {})
                            value = parsed.get("text", parsed.get("steps", value))
                    except (json.JSONDecodeError, AttributeError) as _e:
                        _log.info(
                            "[excel_utils] case_actions_json 旧格式 JSON 解析失败 (非致命): %s",
                            str(_e)[:60],
                        )
                elif "\n[step_checks]\n" in value:
                    text_part, checks_part = value.split("\n[step_checks]\n", 1)
                    value = text_part
                    try:
                        row_dict["_step_checks"] = json.loads(checks_part.strip())
                    except (json.JSONDecodeError, AttributeError) as _e:
                        _log.info(
                            "[excel_utils] step_checks JSON 解析失败 (非致命): %s",
                            str(_e)[:60],
                        )
                lines = value.split("\n")
                parts = []
                for line in lines:
                    line = line.strip()
                    line = re.sub(r"^\d+\.\s*", "", line)
                    if line:
                        parts.append(line)
                value = parts if parts else [value]
            elif attr_name == "verification_status":
                if value is None or str(value).strip() == "":
                    value = ""
            row_dict[attr_name] = value if value is not None else ""
        data.append(row_dict)
    return data
