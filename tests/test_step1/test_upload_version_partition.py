# -*- coding: utf-8 -*-
"""上传文档的版本分区 / 行身份 / 覆盖审计契约。

不变量：
1. 上传文档行按「项目 + 版本」分区 —— 读取不带版本就无法确定分区，必须返回空。
2. 上传文档的行身份是 doc_type，RAG 切片 id 与审计匹配键都从它派生
   （content_hash 只是正文指纹：同正文不同格式会撞车）。
3. 重传同名同类文件 = supersede：旧 doc_type 行必须删除，否则旧文档会一直
   被当成另一份需求重复计入。
4. 数据源失败判定先于索引构建 —— 失败的运行不得写入 rag_chunks。
"""

import pytest

from src.core.upload_documents import fetch_uploaded_documents
from src.extensions.rag_engine.chunking_mixin_mixins._upload import ChunkingUploadMixin
from src.pipeline._step1_phases import check_step1_blocking_failures
from src.pipeline.steps.step1 import rag_coverage_audit


class _CapturingConn:
    """记录 SQL/参数的最小连接替身。"""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []
        self.committed = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


# ── 1. 版本分区是读取的必需维度 ──────────────────────────────


def test_fetch_uploaded_documents_requires_version(monkeypatch):
    import src.core.db_manager as db_manager

    conn = _CapturingConn([{"doc_type": "upload_markdown_abcd1234", "content": "正文"}])
    monkeypatch.setattr(db_manager, "get_connection", lambda: conn)

    assert fetch_uploaded_documents("proj", "") == []

    rows = fetch_uploaded_documents("proj", "v1.0", ["requirement"])

    assert rows and rows[0]["doc_type"] == "upload_markdown_abcd1234"
    sql, params = conn.calls[0]
    assert "version_label = %s" in sql
    assert params == ("proj", "v1.0", "requirement")


# ── 2. 切片 id / 审计匹配键 = 行身份 doc_type ─────────────────


def test_upload_chunk_id_uses_row_identity(monkeypatch):
    import src.extensions.rag_engine.chunking_mixin_mixins._upload as upload_mod

    monkeypatch.setattr(
        upload_mod,
        "fetch_uploaded_documents",
        lambda project, version_label, categories=None: [
            {
                "doc_type": "upload_markdown_abcd1234",
                "filename": "req.md",
                "file_format": "markdown",
                "category": "requirement",
                "content_hash": "deadbeef",
                "content": "需求正文",
            }
        ],
    )

    engine = ChunkingUploadMixin()
    engine._segment_text = lambda text, max_len=500, file_format="": [text]
    engine._extract_keywords = lambda seg: []

    chunks = engine._load_uploaded_file_chunks("proj", "v1.0")

    assert [c["chunk_id"] for c in chunks] == ["upload:upload_markdown_abcd1234:0"]


def test_audit_matches_chunks_by_doc_type_and_version(monkeypatch):
    import src.core.db_manager as db_manager

    conn = _CapturingConn([{"text": "需求正文第一行"}])
    monkeypatch.setattr(db_manager, "get_connection", lambda: conn)
    monkeypatch.setattr(
        rag_coverage_audit,
        "fetch_uploaded_documents",
        lambda project, version_label, categories=None: [
            {
                "doc_type": "upload_markdown_abcd1234",
                "filename": "req.md",
                "file_format": "markdown",
                "category": "requirement",
                "content": "需求正文第一行",
            }
        ],
    )

    result = rag_coverage_audit.audit_upload_coverage("proj", "v1.0")

    assert result["status"] == "ok"
    sql, params = conn.calls[0]
    assert "version_label = %s" in sql
    assert params == ("proj", "v1.0", "upload:upload_markdown_abcd1234:%")

    assert rag_coverage_audit.audit_upload_coverage("proj", "")["status"] == "skipped"


# ── 3. 重传同名文件必须 supersede 旧行 ───────────────────────


def test_upload_supersede_deletes_previous_doc_type(monkeypatch):
    import src.core.db_manager as db_manager
    import src.core.versioning as versioning
    import src.backend.routes.file_upload._upload as upload_route

    conn = _CapturingConn()
    monkeypatch.setattr(db_manager, "get_connection", lambda: conn)
    monkeypatch.setattr(versioning, "current_version_label", lambda project="": "v1.0")

    upload_route._save_to_database(
        "proj", "req.md", "markdown", 0.5, "deadbeefcafe", 4, "正文"
    )

    deletes = [
        (sql, params)
        for sql, params in conn.calls
        if sql.strip().upper().startswith("DELETE")
    ]
    assert conn.committed
    assert len(deletes) == 1
    assert deletes[0][1] == (
        "proj",
        "v1.0",
        "requirement",
        "req.md",
        "upload_markdown_deadbeef",
    )


# ── 4. 阻断判定先于索引构建 ─────────────────────────────────


def test_core_source_failure_blocks_before_index():
    status = {
        "confluence": {"status": "failed", "detail": "boom"},
        "figma": {"status": "failed", "detail": "外部服务不可用"},
        "database": {"status": "ok", "detail": "5 表"},
    }
    err = check_step1_blocking_failures(status, "需求正文")
    assert err and "confluence" in err


def test_skipped_and_unconfigured_sources_do_not_block():
    status = {
        "confluence": {"status": "skipped", "detail": "用户选择跳过"},
        "yapi": {"status": "not_configured"},
        "figma": {"status": "failed", "detail": "辅助源失败只告警"},
    }
    assert check_step1_blocking_failures(status, "需求正文") is None
    # 无任何已配置源且无需求正文 → 阻断
    assert check_step1_blocking_failures(status, "") is not None
