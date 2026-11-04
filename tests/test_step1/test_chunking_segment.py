# -*- coding: utf-8 -*-
"""上传文档切片不变量测试（§P0：覆盖 / 长度 / 无空标题 chunk）。

对应实现：
  src/extensions/rag_engine/chunking_mixin_mixins/_segment.py
  src/extensions/rag_engine/chunking_mixin_mixins/_upload.py
"""
import re

from src.extensions.rag_engine.chunking_mixin_mixins._segment import (
    SmartSegmentMixin,
    _HEADING_PATTERN,
)
from src.extensions.rag_engine.chunking_mixin_mixins._upload import ChunkingUploadMixin


class _Probe(ChunkingUploadMixin, SmartSegmentMixin):
    _UPLOAD_SEGMENT_MAX_LEN = 500
    _EMBED_CHUNK_MAX_LEN = 1000


PROBE = _Probe()
MAX_LEN = 500


def _make_doc(sections: int, body_len: int = 200) -> str:
    parts = ["# 需求文档\n\n本文档描述系统的功能需求。\n"]
    for i in range(1, sections + 1):
        body = ("第%d模块要求：用户可以新增、编辑、删除记录，列表支持分页与筛选，"
                "输入框校验必填与长度，提交后给出成功提示。") % i
        body = (body * (body_len // len(body) + 1))[:body_len]
        parts.append("## %d. 模块%d\n\n%s\n" % (i, i, body))
    return "".join(parts)


def _body_chunks(chunks):
    """排除文档级概览 chunk（非原文，仅用于全局问题检索）。"""
    return [c for c in chunks if not c.startswith("[文档概览]")]


def _norm(text):
    return re.sub(r"\s+", "", text)


def test_coverage_is_complete_for_all_sizes():
    """任意长度文档：正文逐字进入切片，不丢内容、不重复。"""
    for sections in (5, 10, 16, 30):
        doc = _make_doc(sections)
        chunks = _body_chunks(PROBE._segment_text(doc, max_len=MAX_LEN, file_format="markdown"))
        assert chunks, "sections=%d 产出了 0 个 chunk" % sections
        assert _norm("".join(chunks)) == _norm(doc), "sections=%d 内容有丢失或重复" % sections


def test_long_doc_is_split_not_collapsed():
    """超长文档必须保留章节粒度，不能被塌缩成单条摘要。"""
    for sections, min_chunks in ((16, 8), (30, 15)):
        doc = _make_doc(sections)
        chunks = _body_chunks(PROBE._segment_text(doc, max_len=MAX_LEN, file_format="markdown"))
        assert len(chunks) >= min_chunks, "sections=%d 只切出 %d 条" % (sections, len(chunks))


def test_long_doc_has_document_overview_chunk():
    doc = _make_doc(30)
    chunks = PROBE._segment_text(doc, max_len=MAX_LEN, file_format="markdown")
    overview = [c for c in chunks if c.startswith("[文档概览]")]
    assert len(overview) == 1
    assert len(overview[0]) <= MAX_LEN


def test_chunk_never_exceeds_max_len():
    doc = _make_doc(20)
    chunks = _body_chunks(PROBE._segment_text(doc, max_len=MAX_LEN, file_format="markdown"))
    assert max(len(c) for c in chunks) <= MAX_LEN


def test_no_heading_only_chunk():
    doc = _make_doc(10, body_len=20)
    chunks = PROBE._segment_text(doc, max_len=MAX_LEN, file_format="markdown")
    for c in chunks:
        lines = [ln.strip() for ln in c.split("\n") if ln.strip()]
        assert not all(_HEADING_PATTERN.match(ln) for ln in lines), "出现空标题 chunk: %r" % c


def test_preamble_before_first_heading_is_kept():
    doc = "这是标题前的导语，描述背景与目标。\n\n## 1. 功能模块\n\n支持商品新增。\n"
    chunks = PROBE._segment_text(doc, max_len=MAX_LEN, file_format="markdown")
    assert any("导语" in c for c in chunks)
    assert _norm("".join(chunks)) == _norm(doc)


def test_no_natural_boundary_text_is_not_dropped():
    raw = "甲乙丙丁戊己庚辛壬癸" * 500  # 5000 字，无任何标点/换行
    chunks = _body_chunks(PROBE._segment_text(raw, max_len=MAX_LEN, file_format="markdown"))
    assert chunks, "无标点长文本被整篇丢弃"
    assert _norm("".join(chunks)) == _norm(raw)
    assert max(len(c) for c in chunks) <= MAX_LEN


def test_deep_heading_levels_are_recognized():
    """#### 及更深标题也要参与切分（旧正则只认 #~###）。"""
    doc = "".join(
        "#### 子场景%d\n\n用户可以筛选、排序并导出列表，导出格式支持 CSV。\n\n" % i
        for i in range(1, 21)
    )
    chunks = PROBE._segment_text(doc, max_len=MAX_LEN, file_format="markdown")
    assert len(chunks) >= 4
    assert _norm("".join(chunks)) == _norm(doc)


def test_table_split_keeps_header_in_each_chunk():
    rows = ["| 商品名 | 价格 |", "| --- | --- |"] + ["| 商品%d | %d |" % (i, i) for i in range(40)]
    chunks = PROBE._segment_text("\n".join(rows), max_len=MAX_LEN, file_format="csv")
    assert len(chunks) > 1
    assert all(c.split("\n")[0] == "| 商品名 | 价格 |" for c in chunks)
