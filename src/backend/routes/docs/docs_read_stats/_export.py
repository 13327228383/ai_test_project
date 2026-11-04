# -*- coding: utf-8 -*-
"""
统计报告多格式导出函数
"""

import threading
from io import BytesIO
from pathlib import Path
from flask import jsonify, send_file
import logging

_log = logging.getLogger(__name__)

from src.backend.state import _current_project

# ★ 报告格式导出依赖（延迟导入，未安装时不影响原有功能）
_report_extras_available = {}
_report_extras_lock = threading.Lock()


def _check_report_extra(name):
    if name in _report_extras_available:
        return _report_extras_available[name]
    with _report_extras_lock:
        # 双重检查：锁内再次确认未被其他线程写入
        if name in _report_extras_available:
            return _report_extras_available[name]
        try:
            if name == "docx":
                _report_extras_available[name] = True
            elif name == "openpyxl":
                import openpyxl

                _report_extras_available[name] = True
            elif name == "pdf":
                from reportlab.lib import colors, pagesizes
                from reportlab.platypus import (
                    SimpleDocTemplate,
                    Table,
                    TableStyle,
                    Paragraph,
                    Spacer,
                )
                from reportlab.lib.styles import getSampleStyleSheet

                _report_extras_available[name] = True
        except Exception:
            _report_extras_available[name] = False
    return _report_extras_available[name]


def _parse_md_table(lines):
    """将 Markdown 表格行解析为 (headers, rows)。"""
    headers = []
    rows = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            if not in_table:
                headers = cells
                in_table = True
            elif "---" in cells[0] or all("-" in c for c in cells[:2]):
                continue  # 分隔行
            else:
                rows.append(cells)
        else:
            in_table = False
    return headers, rows


def _export_stats_excel(overview, sections, now):
    """导出为 Excel (.xlsx)"""
    if not _check_report_extra("openpyxl"):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "缺少 openpyxl 依赖，请运行: pip install openpyxl",
                }
            ),
            500,
        )
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Border, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "执行概览"

    # 样式
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="4472C4")
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    # 概览表
    ws.append(["测试执行统计报告"])
    ws.append(["项目", _current_project()])
    ws.append(["生成时间", now.strftime("%Y-%m-%d %H:%M:%S")])
    ws.append([])
    ws.append(["指标", "数值"])
    for k, v in [
        ("测试用例总数", overview["total"]),
        ("已验证", overview["executed"]),
        ("通过", overview["passed"]),
        ("失败", overview["failed"]),
        ("跳过", overview["skipped"]),
        ("待验证", overview["pending"]),
        ("达成率", overview["pass_rate"]),
    ]:
        ws.append([k, v])

    # 各维度 Sheet
    for sec_name, sec_lines in sections:
        headers, rows = _parse_md_table(sec_lines)
        if not headers:
            continue
        ws2 = wb.create_sheet(title=sec_name[:31])
        ws2.append(headers)
        for r in rows:
            ws2.append(r)
        # 简单样式
        for cell in ws2[1]:
            cell.font = header_font
            cell.fill = header_fill

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    dl_name = "Test_Stats_%s_%s.xlsx" % (
        _current_project(),
        now.strftime("%Y%m%d_%H%M%S"),
    )
    return send_file(
        bio,
        as_attachment=True,
        download_name=dl_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _export_stats_docx(overview, sections, now):
    """导出为 Word (.docx)"""
    if not _check_report_extra("docx"):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "缺少 python-docx 依赖，请运行: pip install python-docx",
                }
            ),
            500,
        )
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    # 标题
    title = doc.add_heading("测试执行统计报告", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph("项目: %s" % _current_project())
    doc.add_paragraph("生成时间: %s" % now.strftime("%Y-%m-%d %H:%M:%S"))
    doc.add_paragraph()

    # 概览
    doc.add_heading("执行概览", level=1)
    table = doc.add_table(rows=1, cols=2)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text = "指标"
    hdr[1].text = "数值"
    for k, v in [
        ("测试用例总数", overview["total"]),
        ("已验证", overview["executed"]),
        ("通过", overview["passed"]),
        ("失败", overview["failed"]),
        ("跳过", overview["skipped"]),
        ("待验证", overview["pending"]),
        ("达成率", overview["pass_rate"]),
    ]:
        row = table.add_row().cells
        row[0].text = str(k)
        row[1].text = str(v)

    # 各维度
    sec_titles = {
        "module": "目录名称维度",
        "executor": "意图建模作者维度",
        "case_type": "意图类型维度",
        "risk_level": "风险等级维度",
        "version": "版本对比维度",
    }
    for sec_name, sec_lines in sections:
        doc.add_page_break()
        doc.add_heading(sec_titles.get(sec_name, sec_name), level=1)
        headers, rows = _parse_md_table(sec_lines)
        if headers:
            table = doc.add_table(rows=1, cols=len(headers))
            table.style = "Light Grid Accent 1"
            for i, h in enumerate(headers):
                table.rows[0].cells[i].text = h
            for r in rows:
                row = table.add_row().cells
                for i, cell in enumerate(r):
                    if i < len(row):
                        row[i].text = cell

    doc.add_paragraph()
    doc.add_paragraph("报告由 E2E 智能测试引擎自动生成").alignment = (
        WD_ALIGN_PARAGRAPH.CENTER
    )

    bio = BytesIO()
    doc.save(bio)
    bio.seek(0)
    dl_name = "Test_Stats_%s_%s.docx" % (
        _current_project(),
        now.strftime("%Y%m%d_%H%M%S"),
    )
    return send_file(
        bio,
        as_attachment=True,
        download_name=dl_name,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def _export_stats_pdf(overview, sections, now):
    """导出为 PDF"""
    if not _check_report_extra("pdf"):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "缺少 reportlab 依赖，请运行: pip install reportlab",
                }
            ),
            500,
        )
    from reportlab.lib import colors, pagesizes
    from reportlab.platypus import (
        SimpleDocTemplate,
        Table,
        TableStyle,
        Paragraph,
        Spacer,
        PageBreak,
    )
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.units import cm

    # 尝试注册中文字体
    font_name = "Helvetica"
    try:
        # 常见中文字体路径
        font_paths = [
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/simsun.ttc",
            "C:/Windows/Fonts/msyh.ttc",
        ]
        for fp in font_paths:
            if Path(fp).exists():
                pdfmetrics.registerFont(TTFont("CN", fp))
                font_name = "CN"
                break
    except Exception as _e:
        _log.debug("metric: %s", _e, exc_info=True)

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    title_style.fontName = font_name
    h1_style = styles["Heading1"]
    h1_style.fontName = font_name
    body_style = styles["BodyText"]
    body_style.fontName = font_name
    body_style.fontSize = 10

    bio = BytesIO()
    doc = SimpleDocTemplate(
        bio,
        pagesize=pagesizes.A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    story = []

    # 标题
    story.append(Paragraph("<b>测试执行统计报告</b>", title_style))
    story.append(Paragraph("项目: %s" % _current_project(), body_style))
    story.append(
        Paragraph("生成时间: %s" % now.strftime("%Y-%m-%d %H:%M:%S"), body_style)
    )
    story.append(Spacer(1, 0.5 * cm))

    # 概览表
    story.append(Paragraph("<b>执行概览</b>", h1_style))
    overview_data = [["指标", "数值"]]
    for k, v in [
        ("测试用例总数", overview["total"]),
        ("已验证", overview["executed"]),
        ("通过", overview["passed"]),
        ("失败", overview["failed"]),
        ("跳过", overview["skipped"]),
        ("待验证", overview["pending"]),
        ("达成率", overview["pass_rate"]),
    ]:
        overview_data.append([str(k), str(v)])
    t = Table(overview_data, colWidths=[6 * cm, 6 * cm])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), font_name),
                ("FONTSIZE", (0, 0), (-1, 0), 11),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTNAME", (0, 1), (-1, -1), font_name),
                ("FONTSIZE", (0, 1), (-1, -1), 10),
            ]
        )
    )
    story.append(t)

    # 各维度
    sec_titles = {
        "module": "目录名称维度",
        "executor": "意图建模作者维度",
        "case_type": "意图类型维度",
        "risk_level": "风险等级维度",
        "version": "版本对比维度",
    }
    for sec_name, sec_lines in sections:
        story.append(PageBreak())
        story.append(
            Paragraph("<b>%s</b>" % sec_titles.get(sec_name, sec_name), h1_style)
        )
        headers, rows = _parse_md_table(sec_lines)
        if headers:
            data = [headers] + rows
            col_w = min(16 * cm / len(headers), 8 * cm)
            t = Table(data, colWidths=[col_w] * len(headers))
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("FONTNAME", (0, 0), (-1, 0), font_name),
                        ("FONTSIZE", (0, 0), (-1, 0), 10),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("FONTNAME", (0, 1), (-1, -1), font_name),
                        ("FONTSIZE", (0, 1), (-1, -1), 9),
                    ]
                )
            )
            story.append(t)

    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph("报告由 E2E 智能测试引擎自动生成", body_style))

    doc.build(story)
    bio.seek(0)
    dl_name = "Test_Stats_%s_%s.pdf" % (
        _current_project(),
        now.strftime("%Y%m%d_%H%M%S"),
    )
    return send_file(
        bio, as_attachment=True, download_name=dl_name, mimetype="application/pdf"
    )
