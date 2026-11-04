# -*- coding: utf-8 -*-
"""CSV / Excel / Word 解析器 — 从 file_parser.py 拆出，零业务逻辑变更。"""

import csv
import io
from src.core.guarded import guard
import logging
from pathlib import Path
from typing import Any

from ._encoding import decode_file

_log = logging.getLogger(__name__)


class _OfficeFormatsMixin:
    """_parse_csv / _parse_excel / _parse_word — 表格类与文档类格式。"""

    # ── CSV ──

    @staticmethod
    def _parse_csv(path: Path, **options) -> dict[str, Any]:
        """解析 CSV 文件为 Markdown 表格。"""
        # ★ 编码统一走 _encoding.decode_file（原此处另有一套降级链）
        text, encoding = decode_file(path)
        rows = [
            row
            for row in csv.reader(io.StringIO(text, newline=""))
            if any((cell or "").strip() for cell in row)
        ]

        if not rows:
            return {
                "success": True,
                "text": "",
                "format": "csv",
                "metadata": {"encoding": encoding, "rows": 0},
                "empty_hint": "CSV 无有效数据行（文件为空或只有空行）",
            }

        # 转换为 Markdown 表格
        text_lines = []
        text_lines.append("| " + " | ".join(rows[0]) + " |")
        text_lines.append("| " + " | ".join(["---"] * len(rows[0])) + " |")
        for row in rows[1:]:
            text_lines.append("| " + " | ".join(row) + " |")

        text = "\n".join(text_lines)
        return {
            "success": True,
            "text": text,
            "format": "csv",
            "metadata": {
                "rows": len(rows),
                "cols": len(rows[0]) if rows else 0,
                "encoding": encoding,
            },
        }

    # ── Excel ──

    @staticmethod
    def _parse_excel(path: Path, **options) -> dict[str, Any]:
        """解析 Excel 文件为 Markdown 表格（.xls 旧格式由 FileParser 入口统一拒绝）。"""
        try:
            import openpyxl
        except ImportError:
            return {
                "success": False,
                "text": "",
                "format": "excel",
                "metadata": {},
                "error": "缺少 openpyxl 库，请安装: pip install openpyxl",
            }

        wb = None
        try:
            wb = openpyxl.load_workbook(path, data_only=True)
            sheet_name = options.get("sheet_name") or wb.sheetnames[0]
            ws = wb[sheet_name]

            text_lines = []
            rows = list(ws.iter_rows(values_only=True))
            has_content = any(
                any(cell is not None and str(cell).strip() for cell in row)
                for row in rows
            )

            if not has_content:
                return {
                    "success": True,
                    "text": "",
                    "format": "excel",
                    "metadata": {"sheet": sheet_name, "rows": len(rows)},
                    "empty_hint": "工作表 '%s' 无有效数据（空表）" % sheet_name,
                }

            # 转换为 Markdown 表格
            text_lines.append(
                "| "
                + " | ".join(str(c) if c is not None else "" for c in rows[0])
                + " |"
            )
            text_lines.append("| " + " | ".join(["---"] * len(rows[0])) + " |")
            for row in rows[1:]:
                text_lines.append(
                    "| "
                    + " | ".join(str(c) if c is not None else "" for c in row)
                    + " |"
                )

            text = "\n".join(text_lines)
            return {
                "success": True,
                "text": text,
                "format": "excel",
                "metadata": {
                    "sheet": sheet_name,
                    "rows": len(rows),
                    "cols": len(rows[0]) if rows else 0,
                },
            }
        finally:
            if wb:
                try:
                    wb.close()
                except Exception as _pass_e:
                    _log.warning(
                        "office_formats.py:83 异常被吞: %s", _pass_e, exc_info=True
                    )

    # ── Word ──

    @staticmethod
    def _word_heading_level(style_name: str) -> int:
        """Word 段落样式 → Markdown 标题层级（Heading 1 / 标题 1 → 1）。"""
        name = (style_name or "").strip().lower()
        for prefix in ("heading ", "标题 "):
            if name.startswith(prefix):
                tail = name[len(prefix) :].strip()
                if tail.isdigit():
                    return max(1, min(6, int(tail)))
        return 0

    @staticmethod
    def _parse_word(path: Path, **options) -> dict[str, Any]:
        """解析 Word 文档为 Markdown（保留标题层级，供切片器按大纲切分）。

        ★ 2026-07-13: 同时提取嵌入图片（供视觉识别模块使用）。
        ★ 2026-10-24: 标题样式输出为 #/##/###；此前全部拍平成纯文本，
          切片器拿不到章节结构，超长文档只能按长度切。
        """
        try:
            from docx import Document
        except ImportError:
            return {
                "success": False,
                "text": "",
                "format": "word",
                "metadata": {},
                "error": "缺少 python-docx 库，请安装: pip install python-docx",
            }

        doc = Document(path)
        paragraphs = []
        heading_count = 0
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            level = _OfficeFormatsMixin._word_heading_level(
                getattr(para.style, "name", "") if para.style else ""
            )
            if level:
                heading_count += 1
                paragraphs.append("#" * level + " " + text)
            else:
                paragraphs.append(text)

        # 提取表格
        for table in doc.tables:
            paragraphs.append("\n--- 表格 ---")
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells)
                paragraphs.append(row_text)
            paragraphs.append("---\n")

        text = "\n\n".join(paragraphs)

        # ★ 提取嵌入图片（inline shapes）
        image_bytes_list = []
        with guard("_parse_word: for idx, shape in enumerate(doc.inli", log=_log):
            for idx, shape in enumerate(doc.inline_shapes):
                if shape.type is not None and str(shape.type) == "PICTURE":
                    rid = shape._inline.graphic.graphicData.pic.blipFill.blip.embed
                    img_part = doc.part.related_parts[rid]
                    img_bytes = img_part.blob
                    img_name = (
                        img_part.partname.split("/")[-1]
                        if img_part.partname
                        else "image_%d" % idx
                    )
                    image_bytes_list.append((img_name, img_bytes))

        return {
            "success": True,
            "text": text,
            "format": "word",
            "metadata": {
                "paragraphs": len(doc.paragraphs),
                "tables": len(doc.tables),
                "headings": heading_count,
            },
            "empty_hint": "Word 文档无可提取文本（可能只有图片），请补充文字内容",
            "image_bytes_list": image_bytes_list,
        }  # ★ 嵌入图片
