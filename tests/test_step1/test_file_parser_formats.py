# -*- coding: utf-8 -*-
"""Step1 文件解析器测试（Word 标题层级 / PDF 支持）。

对应实现：src/pipeline/steps/step1/file_parser/
"""
import pytest

from src.pipeline.steps.step1.file_parser import FileParser
from src.pipeline.steps.step1.file_parser.office_formats import _OfficeFormatsMixin


def test_word_headings_are_preserved_as_markdown(tmp_path):
    docx = pytest.importorskip("docx")
    path = tmp_path / "req.docx"
    document = docx.Document()
    document.add_heading("SmartShop 需求文档", level=1)
    document.add_heading("1. 项目概述", level=2)
    document.add_paragraph("系统用于商品管理。")
    document.add_heading("2. 功能模块", level=2)
    document.save(str(path))

    result = FileParser.parse(str(path))

    assert result["success"], result.get("error")
    assert "# SmartShop 需求文档" in result["text"]
    assert "## 1. 项目概述" in result["text"]
    assert "## 2. 功能模块" in result["text"]
    assert result["metadata"]["headings"] == 3


def test_word_heading_level_mapping():
    assert _OfficeFormatsMixin._word_heading_level("Heading 1") == 1
    assert _OfficeFormatsMixin._word_heading_level("Heading 3") == 3
    assert _OfficeFormatsMixin._word_heading_level("标题 2") == 2
    assert _OfficeFormatsMixin._word_heading_level("Normal") == 0
    assert _OfficeFormatsMixin._word_heading_level("") == 0


def test_pdf_is_registered():
    assert FileParser.EXTENSION_MAP[".pdf"] == "pdf"


def test_pdf_parse_returns_text(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "req.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "SmartShop 商品管理需求", fontname="china-s", fontsize=16)
    page.insert_text((72, 110), "支持新增、编辑、删除商品。", fontname="china-s", fontsize=11)
    pdf.save(str(path))
    pdf.close()

    result = FileParser.parse(str(path))

    assert result["success"], result.get("error")
    assert result["format"] == "pdf"
    assert "商品管理需求" in result["text"]
    assert result["metadata"]["engine"] in ("pymupdf4llm", "pymupdf")
