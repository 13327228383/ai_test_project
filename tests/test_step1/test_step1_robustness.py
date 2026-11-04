# -*- coding: utf-8 -*-
"""Step1 健壮性测试：解析器诚实失败、编码判定、需求持久化确定性、切片覆盖审计。

对应实现：
- src/pipeline/steps/step1/file_parser/_encoding.py
- src/pipeline/steps/step1/file_parser/{text,office}_formats.py
- src/pipeline/fetchers/persist.py
- src/pipeline/helpers.py::load_canonical_requirement_text
- src/pipeline/steps/step1/rag_coverage_audit.py
"""

import hashlib

import pytest

from src.pipeline.steps.step1.file_parser import FileParser
from src.pipeline.steps.step1.rag_coverage_audit import audit_document


# ── 解析器：不支持/空内容必须诚实失败 ──────────────────────────────


def test_legacy_doc_reports_unsupported(tmp_path):
    path = tmp_path / "req.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0 legacy binary")
    result = FileParser.parse(str(path))
    assert result["success"] is False
    assert ".docx" in result["error"]


def test_legacy_xls_reports_unsupported(tmp_path):
    path = tmp_path / "req.xls"
    path.write_bytes(b"legacy xls")
    result = FileParser.parse(str(path))
    assert result["success"] is False
    assert ".xlsx" in result["error"]


def test_empty_pdf_is_failure_not_empty_success(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "scan.pdf"
    pdf = pymupdf.open()
    pdf.new_page()  # 无任何文本 → 等价扫描件
    pdf.save(str(path))
    pdf.close()

    result = FileParser.parse(str(path))

    assert result["success"] is False
    assert "OCR" in result["error"] or "无可提取文本" in result["error"]


def test_legacy_formats_not_declared_as_parseable():
    """旧格式只能出现在唯一的转换登记表里，不能同时出现在可解析注册表中。"""
    for ext in (".doc", ".xls"):
        assert ext not in FileParser.EXTENSION_MAP
        assert ext in FileParser.REQUIRES_CONVERSION


def test_every_registered_format_has_parser():
    """注册表与派发表同源：任何登记格式都必须有解析方法，否则入口必然假失败。"""
    for ext, fmt in FileParser.EXTENSION_MAP.items():
        assert fmt in FileParser.PARSER_METHODS, "格式 %s(%s) 未挂解析方法" % (fmt, ext)


def test_pptx_is_dispatched(tmp_path):
    pptx = pytest.importorskip("pptx")
    path = tmp_path / "req.pptx"
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "商品管理需求"
    prs.save(str(path))

    result = FileParser.parse(str(path))

    assert result["success"], result.get("error")
    assert result["format"] == "pptx"
    assert "商品管理需求" in result["text"]


def test_empty_markdown_is_failure(tmp_path):
    """空文本判定统一在 FileParser 入口：任何格式的空文件都不得以成功返回。"""
    path = tmp_path / "empty.md"
    path.write_text("   \n\n", encoding="utf-8")
    result = FileParser.parse(str(path))
    assert result["success"] is False
    assert "无可提取文本" in result["error"]


def test_empty_csv_is_failure(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("\n\n", encoding="utf-8")
    result = FileParser.parse(str(path))
    assert result["success"] is False
    assert "无有效数据" in result["error"]


def test_empty_excel_is_failure(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "empty.xlsx"
    wb = openpyxl.Workbook()
    wb.save(str(path))

    result = FileParser.parse(str(path))

    assert result["success"] is False
    assert "无有效数据" in result["error"]


# ── 编码判定：BOM/中文编码不得静默乱码 ────────────────────────────


def test_utf16_bom_text_decodes(tmp_path):
    path = tmp_path / "utf16.txt"
    path.write_bytes("商品管理需求：新增商品".encode("utf-16"))

    result = FileParser.parse(str(path))

    assert result["success"]
    assert "商品管理需求" in result["text"]
    assert result["metadata"]["encoding"].startswith("utf-16")


def test_gb18030_text_decodes(tmp_path):
    path = tmp_path / "gb.txt"
    path.write_bytes("商品管理需求：删除商品".encode("gb18030"))

    result = FileParser.parse(str(path))

    assert result["success"]
    assert "商品管理需求" in result["text"]
    assert result["metadata"]["encoding"] == "gb18030"


def test_csv_uses_shared_decoder(tmp_path):
    path = tmp_path / "gb.csv"
    path.write_bytes("名称,操作\n商品,新增\n".encode("gb18030"))

    result = FileParser.parse(str(path))

    assert result["success"], result.get("error")
    assert "商品" in result["text"]
    assert result["metadata"]["encoding"] == "gb18030"


# ── 需求持久化：头部确定性 + 哈希覆盖落库内容 ────────────────────


class _FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_persist_requirements_is_deterministic_and_hashes_content(monkeypatch):
    import src.core.db_manager as dbm
    import src.users.manager as um
    from src.pipeline.fetchers.persist import _Step1PersistMixin

    conn = _FakeConn()
    monkeypatch.setattr(dbm, "get_connection", lambda: conn)
    monkeypatch.setattr(um, "record_document_version", lambda *a, **k: None)

    mixin = _Step1PersistMixin()
    mixin._persist_requirements(False, "## 需求\n商品新增", {}, "v1")
    first_content, first_hash = conn.calls[0][1][2], conn.calls[0][1][3]
    mixin._persist_requirements(False, "## 需求\n商品新增", {}, "v1")
    second_content, second_hash = conn.calls[2][1][2], conn.calls[2][1][3]

    assert "生成时间" not in first_content
    assert first_content == second_content
    assert first_hash == second_hash
    assert first_hash == hashlib.sha256(first_content.encode()).hexdigest()


# ── canonical 需求正文：头部剥离单一实现 ──────────────────────────


def test_load_canonical_requirement_text_strips_header(monkeypatch):
    import src.core.db_manager as dbm
    from src.pipeline.helpers import load_canonical_requirement_text

    row = {
        "content": "# 需求文档（上传文件）\n\n> 来源页面: P1\n\n---\n\n## 正文\n商品新增"
    }

    class _Conn:
        def execute(self, sql, params=()):
            return self

        def fetchone(self):
            return row

        def close(self):
            pass

    monkeypatch.setattr(dbm, "get_connection", lambda: _Conn())

    assert load_canonical_requirement_text("p", "v1") == "## 正文\n商品新增"


# ── 切片覆盖审计 ──────────────────────────────────────────────


def test_coverage_full_when_all_text_indexed():
    source = "# 模块A\n\n新增商品\n删除商品"
    result = audit_document(source, ["# 模块A", "新增商品\n删除商品"])
    assert result["ratio"] == 1.0
    assert result["missing"] == []


def test_coverage_detects_lost_section():
    source = "# 模块A\n\n新增商品\n\n# 模块B\n\n批量导入"
    result = audit_document(source, ["# 模块A\n\n新增商品"])
    assert result["ratio"] < 1.0
    assert any("批量导入" in line for line in result["missing"])


def test_coverage_tolerates_repeated_table_header():
    source = "| 名称 | 操作 |\n| --- | --- |\n| 商品 | 新增 |\n| 订单 | 删除 |"
    segments = [
        "| 名称 | 操作 |\n| --- | --- |\n| 商品 | 新增 |",
        "| 名称 | 操作 |\n| --- | --- |\n| 订单 | 删除 |",
    ]
    assert audit_document(source, segments)["ratio"] == 1.0


def test_coverage_tolerates_intentional_dedupe():
    """RAG 构建会合并重复段落：重复内容只保留一份不算丢失，不得误判为缺口。"""
    section = "## 通用规则\n\n所有操作需二次确认"
    source = section + "\n\n## 模块A\n\n新增商品\n\n" + section
    chunks = [section, "## 模块A\n\n新增商品"]

    result = audit_document(source, chunks)

    assert result["missing_lines"] == 0
    assert result["missing"] == []
    assert result["ratio"] == 1.0
    # 字符多重集口径仍如实记录重复内容被合并
    assert result["char_ratio"] < 1.0


# ── RAG 构建入口：不得按调用参数短路，"该源有没有数据"由各 builder 判定 ──


def test_build_index_always_dispatches_all_sources(monkeypatch):
    """build_index 不能因调用参数为空就跳过 DB 驱动源。

    uploads/features/figma 的数据只在库里，调用参数永远为空（Step1 不传）。
    入口若据此短路，新上传文档就永远进不了索引（Step2/Step3 只能读旧快照）。
    契约：派发无短路，判定权在各 builder 自身。
    """
    import src.extensions.rag_engine.engine_core as ec

    engine = ec.RagEngine()
    monkeypatch.setattr(engine, "_get_project_name", lambda: "proj_x")
    monkeypatch.setattr(engine, "_get_project_version", lambda: "v1.0")
    monkeypatch.setattr(engine, "_load_chunks", lambda project="", version_label="": None)
    monkeypatch.setattr(engine, "_save_to_db", lambda **kwargs: None)
    monkeypatch.setattr(engine, "_build_bm25_index", lambda: None)
    monkeypatch.setattr(engine, "_detect_best_metric", lambda: None)

    dispatched = []

    def _recorder(name, result=None):
        def _fn(*args, **kwargs):
            dispatched.append(name)
            return result if result is not None else []

        return _fn

    for mod_name, label in (
        ("_mod_build_dom_chunks", "dom"),
        ("_mod_build_api_chunks", "api"),
        ("_mod_build_db_chunks", "db"),
        ("_mod_build_prototype_chunks", "prototype_images"),
        ("_mod_build_feature_chunks", "features"),
        ("_mod_build_figma_index", "figma"),
    ):
        monkeypatch.setattr(ec, mod_name, _recorder(label))
    monkeypatch.setattr(
        engine,
        "_load_uploaded_file_chunks",
        _recorder(
            "uploads",
            [{"chunk_id": "upload:h:0", "type": "upload_req", "text": "新上传的需求"}],
        ),
    )

    count = engine.build_index(use_embeddings=False)  # 所有调用参数为空

    assert set(dispatched) == {
        "dom",
        "api",
        "db",
        "prototype_images",
        "features",
        "uploads",
        "figma",
    }
    assert count == 1
    assert [c["chunk_id"] for c in engine._chunks] == ["upload:h:0"]

