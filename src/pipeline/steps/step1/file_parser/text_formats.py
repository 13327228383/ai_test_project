# -*- coding: utf-8 -*-
"""Markdown / SQL / TXT 解析器（纯文本类）— 从 file_parser.py 拆出，零业务逻辑变更。"""

from pathlib import Path
from typing import Any
import logging

from ._encoding import decode_file

_log = logging.getLogger(__name__)


class _TextFormatsMixin:
    """_parse_markdown / _parse_sql / _parse_text — 纯文本/DDL 类格式。"""

    # ── Markdown ──

    @staticmethod
    def _parse_markdown(path: Path, **options) -> dict[str, Any]:
        """直接读取 Markdown 文件。"""
        text, encoding = decode_file(path)
        return {
            "success": True,
            "text": text,
            "format": "markdown",
            "metadata": {"size": len(text), "encoding": encoding},
        }

    # ── SQL ──

    @staticmethod
    def _parse_sql(path: Path, **options) -> dict[str, Any]:
        """解析 SQL DDL 文件，提取表结构、字段、关系、种子数据。"""
        import re

        # ★ 编码降级：统一走 _encoding.decode_file（BOM → UTF-8 → GB18030 → latin-1）
        sql_text, _encoding = decode_file(path)

        lines = []
        tables_count = 0
        cols_count = 0
        seed_count = 0

        # 1. 解析 CREATE TABLE
        pattern = (
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?(\w+)[\"`]?\s*\((.*?)\);"
        )
        for match in re.finditer(pattern, sql_text, re.DOTALL | re.IGNORECASE):
            table_name = match.group(1)
            body = match.group(2)
            tables_count += 1

            lines.append("## 表: %s" % table_name)

            # 提取列
            col_pattern = r"[\"`]?(\w+)[\"`]?\s+(\w+(?:\([^)]*\))?)\s*(.*)"
            for col_match in re.finditer(
                col_pattern, body, re.MULTILINE | re.IGNORECASE
            ):
                col_name = col_match.group(1).upper()
                col_type = col_match.group(2).upper()
                rest = col_match.group(3).strip()

                if col_name in (
                    "PRIMARY",
                    "INDEX",
                    "UNIQUE",
                    "FOREIGN",
                    "CONSTRAINT",
                    "KEY",
                    "CHECK",
                    "AUTO_INCREMENT",
                ):
                    continue

                cols_count += 1
                comment = ""
                comment_match = re.search(r"COMMENT\s+'([^']*)'", rest, re.IGNORECASE)
                if comment_match:
                    comment = " — %s" % comment_match.group(1)

                lines.append("  - %s (%s)%s" % (col_name, col_type, comment))

            lines.append("")

        # 2. 解析 FOREIGN KEY
        fk_pattern = r"FOREIGN\s+KEY\s*\([^)]+\)\s*REFERENCES\s+[\"`]?(\w+)[\"`]?"
        fks = re.findall(fk_pattern, sql_text, re.IGNORECASE)
        if fks:
            lines.append("## 外键关系")
            for fk in fks:
                lines.append("  - REFERENCES %s" % fk)
            lines.append("")

        # 3. 解析 INSERT（种子数据）
        insert_pattern = (
            r"INSERT\s+INTO\s+[\"`]?(\w+)[\"`]?\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)"
        )
        inserts = list(re.finditer(insert_pattern, sql_text, re.DOTALL | re.IGNORECASE))
        if inserts:
            lines.append("## 种子数据")
            for match in inserts[:20]:  # 最多显示20条
                table_name = match.group(1)
                cols = match.group(2).strip()
                vals = match.group(3).strip()
                seed_count += 1
                lines.append(
                    "  - %s: INSERT (%s) VALUES (%s)" % (table_name, cols, vals)
                )
            if len(inserts) > 20:
                lines.append("  ... 共 %d 条" % len(inserts))
            lines.append("")

        text = "\n".join(lines)
        # ★ 兜底：结构化解析无产出时（如纯 ALTER/INDEX/VIEW 等非 CREATE TABLE 语句），
        # 降级为原文保留，确保 SQL 内容不丢失。
        if not text.strip():
            return {
                "success": True,
                "text": sql_text,
                "format": "sql",
                "metadata": {"fallback": "raw", "size": len(sql_text)},
            }
        return {
            "success": True,
            "text": text,
            "format": "sql",
            "metadata": {
                "tables": tables_count,
                "columns": cols_count,
                "foreign_keys": len(fks),
                "seed_rows": seed_count,
            },
        }

    # ── TXT ──

    @staticmethod
    def _parse_text(path: Path, **options) -> dict[str, Any]:
        """解析 TXT 纯文本文件，原文保留。"""
        text, encoding = decode_file(path)
        max_len = options.get("max_length", 0)
        if max_len and len(text) > max_len:
            text = text[:max_len] + "\n\n... (截断，共 %d 字符)" % len(text)
        return {
            "success": True,
            "text": text,
            "format": "text",
            "metadata": {
                "chars": len(text),
                "lines": text.count("\n") + 1,
                "encoding": encoding,
            },
        }

    # ── PDF ──

    @staticmethod
    def _parse_pdf(path: Path, **options) -> dict[str, Any]:
        """解析 PDF 为 Markdown（优先 pymupdf4llm，保留标题/阅读序）。

        pymupdf4llm 不可用或转换失败时降级为 pymupdf 纯文本抽取（仍保留分页边界），
        两者都缺失则返回明确依赖提示，不静默产出空文本。
        """
        text = ""
        engine = ""

        try:
            import pymupdf4llm

            text = pymupdf4llm.to_markdown(str(path)) or ""
            engine = "pymupdf4llm"
        except ImportError:
            pass
        except Exception as _md_e:
            _log.warning("[text_formats] pymupdf4llm 转换失败，降级纯文本: %s", str(_md_e)[:120])

        if not text.strip():
            try:
                import pymupdf

                doc = pymupdf.open(str(path))
                try:
                    text = "\n\n".join(page.get_text("text") for page in doc)
                finally:
                    doc.close()
                engine = "pymupdf"
            except ImportError:
                return {
                    "success": False,
                    "text": "",
                    "format": "pdf",
                    "metadata": {},
                    "error": "缺少 pymupdf 依赖，请安装: pip install pymupdf",
                }
            except Exception as _pdf_e:
                return {
                    "success": False,
                    "text": "",
                    "format": "pdf",
                    "metadata": {},
                    "error": "PDF 解析失败: %s" % str(_pdf_e)[:120],
                }

        if not text.strip():
            # 扫描件/图片版 PDF：给出成因与处置指引，由 FileParser 入口统一判失败
            return {
                "success": True,
                "text": "",
                "format": "pdf",
                "metadata": {"engine": engine or "", "chars": 0},
                "empty_hint": (
                    "PDF 无可提取文本（多半是扫描件/图片版）；本地上传不做 OCR，"
                    "请改用可复制文本的 PDF，或改为 Word/Markdown 上传"
                ),
            }

        return {
            "success": True,
            "text": text,
            "format": "pdf",
            "metadata": {
                "chars": len(text),
                "engine": engine,
            },
        }

    # ★ S1-001: PPTX 格式支持
    @staticmethod
    def _parse_pptx(path: Path, **options) -> dict[str, Any]:
        """解析 PPTX 演示文稿，提取所有幻灯片文本。"""
        try:
            from pptx import Presentation

            prs = Presentation(str(path))
            slides = []
            for i, slide in enumerate(prs.slides, 1):
                texts = []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            t = para.text.strip()
                            if t:
                                texts.append(t)
                    elif shape.has_table:
                        for row in shape.table.rows:
                            row_texts = [
                                x for cell in row.cells if (x := cell.text.strip())
                            ]
                            if row_texts:
                                texts.append(" | ".join(row_texts))
                if texts:
                    slides.append("## Slide %d\n\n%s" % (i, "\n".join(texts)))
            text = "\n\n".join(slides) if slides else ""
            if not text.strip():
                # 幻灯片内只有图片/图形：给出成因与处置指引，由 FileParser 入口统一判失败
                return {
                    "success": True,
                    "text": "",
                    "format": "pptx",
                    "metadata": {"slides": len(prs.slides), "chars": 0},
                    "empty_hint": "PPTX 无可提取文本（幻灯片内只有图片/图形），请补充文字内容",
                }
            max_len = options.get("max_length", 0)
            if max_len and len(text) > max_len:
                text = text[:max_len] + "\n\n... (截断，共 %d 字符)" % len(text)
            return {
                "success": True,
                "text": text,
                "format": "pptx",
                "metadata": {"slides": len(prs.slides), "chars": len(text)},
            }
        except ImportError:
            _log.warning(
                "[text_formats] python-pptx 未安装，无法解析 PPTX: %s", path.name
            )
            return {"success": False, "error": "python-pptx 未安装，无法解析 PPTX 文件"}
        except Exception as e:
            _log.warning(
                "[text_formats] PPTX 解析失败: %s → %s", path.name, str(e)[:80]
            )
            return {"success": False, "error": "PPTX 解析失败: %s" % str(e)[:120]}
