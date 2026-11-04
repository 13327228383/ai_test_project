# -*- coding: utf-8 -*-
"""
Step1 文件上传解析器
===================
支持 CSV / Excel / Markdown / Word / PDF / XMind / JSON / SQL / TXT / PPTX 格式的需求文档解析。

★ P1: 统一解析接口，输出标准化文本供 Step2/Step3 使用。

【2026-06-18 重构】原 406 行单文件按格式族拆分：
- office_formats.py — _parse_csv / _parse_excel / _parse_word（表格类与文档类）
- tree_formats.py   — _parse_xmind / _parse_xmind_json / _parse_xmind_xml / _parse_json（树状/结构化）
- text_formats.py   — _parse_markdown / _parse_sql / _parse_text（纯文本/DDL）
- __init__.py       — facade FileParser + 扩展名映射 + parse 统一入口 + parse_file/parse_files 便捷函数
"""

from pathlib import Path
from typing import Any
import logging

_log = logging.getLogger(__name__)


from .office_formats import _OfficeFormatsMixin
from .tree_formats import _TreeFormatsMixin
from .text_formats import _TextFormatsMixin


class FileParser(_OfficeFormatsMixin, _TreeFormatsMixin, _TextFormatsMixin):
    """多格式文件解析器。

    格式注册分两张表，语义互不重叠：
    - EXTENSION_MAP：扩展名 → 解析格式（本机可解析的格式全集）
    - REQUIRES_CONVERSION：本机解析器明确不支持的旧格式 → (格式, 用户可执行的转换指引)

    两张表都由 `parse()` 单点消费；解析器方法内不再重复判断扩展名。
    """

    # 扩展名 → 解析格式
    EXTENSION_MAP = {
        ".csv": "csv",
        ".xlsx": "excel",
        ".md": "markdown",
        ".markdown": "markdown",
        ".docx": "word",
        ".xmind": "xmind",
        ".json": "json",
        ".sql": "sql",
        ".txt": "text",
        ".pptx": "pptx",
        ".pdf": "pdf",
    }

    # 旧版二进制 Office 格式（.doc/.xls）不在 OOXML 支持范围内：
    # 在此一次性拒绝并给出转换指引，避免各解析器内部再写一份"后缀判断"
    REQUIRES_CONVERSION = {
        ".doc": ("word", "旧版 .doc 不受支持，请在 Word 中另存为 .docx 后重新上传"),
        ".xls": ("excel", "旧版 .xls 不受支持，请在 Excel 中另存为 .xlsx 后重新上传"),
    }

    # 格式 → 解析方法名（注册表与派发表同源，新增格式漏挂解析方法会被契约测试拦住）
    PARSER_METHODS = {
        "csv": "_parse_csv",
        "excel": "_parse_excel",
        "markdown": "_parse_markdown",
        "word": "_parse_word",
        "xmind": "_parse_xmind",
        "json": "_parse_json",
        "sql": "_parse_sql",
        "text": "_parse_text",
        "pdf": "_parse_pdf",
        "pptx": "_parse_pptx",
    }

    @staticmethod
    def _enforce_text_contract(result: dict[str, Any]) -> dict[str, Any]:
        """解析结果文本契约（唯一判定点）：成功但无可提取文本 ⇒ 明确失败。

        空文本的成因（扫描件 PDF、空表、纯图片 Word/PPTX、空文件）由各解析器
        以 `empty_hint` 给出面向用户的指引，此处只做一次成功/失败决策，
        避免每个解析器各自返回 success=False 造成语义多处推导。
        """
        if not result.get("success"):
            result.pop("empty_hint", None)
            return result
        if str(result.get("text") or "").strip():
            result.pop("empty_hint", None)
            return result
        hint = result.pop("empty_hint", "") or (
            "文件无可提取文本内容，请补充可复制的文字内容后重新上传"
        )
        result["success"] = False
        result["text"] = ""
        result["error"] = hint
        return result

    @classmethod
    def parse(cls, file_path: str, **options) -> dict[str, Any]:
        """
        统一解析入口。

        Args:
            file_path: 文件路径
            **options: 解析选项
                - sheet_name: Excel 工作表名（默认第一个）
                - max_length: 最大输出长度（默认无限制）

        Returns:
            {"success": bool, "text": str, "format": str, "metadata": dict}
        """
        path = Path(file_path)
        if not path.exists():
            return {
                "success": False,
                "text": "",
                "format": "",
                "metadata": {},
                "error": "文件不存在: %s" % file_path,
            }

        ext = path.suffix.lower()
        if ext in cls.REQUIRES_CONVERSION:
            fmt, hint = cls.REQUIRES_CONVERSION[ext]
            _log.warning("文件格式需转换后重新上传 [%s]: %s", fmt, path.name)
            return {
                "success": False,
                "text": "",
                "format": fmt,
                "metadata": {},
                "error": hint,
            }

        fmt = cls.EXTENSION_MAP.get(ext)
        if not fmt:
            return {
                "success": False,
                "text": "",
                "format": "",
                "metadata": {},
                "error": "不支持的文件格式: %s" % ext,
            }

        method_name = cls.PARSER_METHODS.get(fmt)
        if not method_name:
            return {
                "success": False,
                "text": "",
                "format": fmt,
                "metadata": {},
                "error": "未实现的解析器: %s" % fmt,
            }

        try:
            result = getattr(cls, method_name)(path, **options)
        except Exception as e:
            _log.error("文件解析失败 [%s]: %s", fmt, e)
            return {
                "success": False,
                "text": "",
                "format": fmt,
                "metadata": {},
                "error": "解析异常: %s" % str(e),
            }
        return cls._enforce_text_contract(result)


# ══════════════════════════════════════════════════════════
# 便捷函数
# ══════════════════════════════════════════════════════════


def parse_file(file_path: str, **options) -> dict[str, Any]:
    """便捷函数：解析单个文件。"""
    return FileParser.parse(file_path, **options)


def parse_files(file_paths: list[str], **options) -> list[dict[str, Any]]:
    """便捷函数：批量解析多个文件。"""
    results = []
    for fp in file_paths:
        results.append(FileParser.parse(fp, **options))
    return results
