# -*- coding: utf-8 -*-
"""
Excel I/O 工具函数（导入辅助 + 导出保存）
==========================================
从 ExcelHandler 类中提取的静态方法，仅依赖 openpyxl，无 self 状态。
用于 _ensure_columns / _save_workbook_with_retry。
"""

import logging
from pathlib import Path

from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

_log = logging.getLogger(__name__)


def ensure_columns(ws, columns):
    """兼容旧表：缺少新增列时追加表头到末尾，不重排列。

    仅在显式导入 Excel 时使用；运行时 MySQL 表结构由 schema.sql 管理。
    """
    existing_headers = [
        str(ws.cell(row=1, column=i).value or "").strip()
        for i in range(1, ws.max_column + 1)
    ]
    header_font = Font(name="微软雅黑", bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(
        start_color="4472C4", end_color="4472C4", fill_type="solid"
    )
    header_alignment = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    changed = False
    for _, col_name, col_width in columns:
        if col_name in existing_headers:
            continue
        col_idx = ws.max_column + 1
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border
        ws.column_dimensions[get_column_letter(col_idx)].width = col_width
        existing_headers.append(col_name)
        changed = True
    return changed


def save_workbook_with_retry(wb, save_path, max_retries=3):
    """仅用于显式 Excel 导出时的保存。主链路已不再生成 Excel。

    Args:
        wb: openpyxl Workbook 实例
        save_path: 保存路径字符串或 Path
        max_retries: 最大重试次数（默认 3）
    """
    import time as _time

    target = str(save_path)
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    last_error = None
    for attempt in range(max_retries):
        try:
            wb.save(target)
            wb.close()
            return
        except PermissionError as e:
            last_error = e
            if attempt < max_retries - 1:
                _time.sleep(1.0)
        except Exception as e:
            last_error = e
            break
    wb.close()
    _log.error(
        "##HTML:##<span style='color:#ef4444;font-weight:bold'>⚠ Excel 写入失败（%d 次重试后仍失败）: %s</span>",
        max_retries,
        last_error,
    )
    _log.info("  请关闭 Excel 中打开的文件后重试: %s", Path(target).name)
