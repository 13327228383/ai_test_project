# -*- coding: utf-8 -*-
"""
文档中心 — 统计报告导出路由 (package facade)
多维度统计报告 + 多格式导出（md/xlsx/docx/pdf）。
"""

from ._stats import api_docs_execution_stats
from ._export import (
    _export_stats_excel,
    _export_stats_docx,
    _export_stats_pdf,
    _check_report_extra,
)

__all__ = [
    "api_docs_execution_stats",
    "_export_stats_excel",
    "_export_stats_docx",
    "_export_stats_pdf",
    "_check_report_extra",
]
