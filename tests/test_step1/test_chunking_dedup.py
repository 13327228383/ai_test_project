# -*- coding: utf-8 -*-
"""单元测试：切片去重的章节锚点规则。

需求文档里"2.1/2.2/2.3 商品子模块"正文高度相似但各自是独立需求。
第二层语义去重若把它们合并，正文覆盖率会从 100% 塌到个位数（真实流水线实测 9.4%）。
判据：首个标题行不同的切片 = 不同内容单元，不参与相似度合并；
      同一章节内的近似重复仍须合并（去重能力不能被这次修复废掉）。
"""
from src.extensions.rag_engine.chunking_mixin_mixins._dedup import ChunkingDedupMixin

_BODY = (
    "用户可以新增、编辑、删除商品记录，列表支持分页与关键字筛选，价格必须为大于 0 的数值，"
    "库存不得为负数，提交后页面给出成功提示，删除需要二次确认，越权访问返回 403，"
    "未登录访问跳转登录页，错误提示需要明确到字段级，日志不得输出用户密码明文。"
) * 2


def _chunk(cid: str, text: str) -> dict:
    return {"chunk_id": cid, "type": "upload", "text": text}


def test_section_anchor_is_first_heading_line():
    assert ChunkingDedupMixin._section_anchor("### 2.1 商品管理\n\n" + _BODY) == "### 2.1 商品管理"
    assert ChunkingDedupMixin._section_anchor("前言内容\n\n## 3. 非功能需求\n正文") == "## 3. 非功能需求"
    assert ChunkingDedupMixin._section_anchor("无标题的纯正文") == ""


def test_same_text_different_ids_is_deduped():
    chunks = [_chunk("a:1", "## 1. 概述\n" + _BODY), _chunk("a:2", "## 1. 概述\n" + _BODY)]
    assert len(ChunkingDedupMixin._deduplicate_chunks(chunks)) == 1


def test_near_duplicate_in_same_section_is_merged():
    """同一章节内的近似重复（仅末尾差异）必须合并，保留更长者。"""
    short = "## 1. 概述\n" + _BODY
    longer = short + "补充一句。"
    kept = ChunkingDedupMixin._deduplicate_chunks([_chunk("a:1", longer), _chunk("a:2", short)])
    assert len(kept) == 1
    assert kept[0]["chunk_id"] == "a:1"


def test_similar_text_in_different_sections_both_kept():
    """不同章节锚点 → 独立需求，相似度再高也不合并（零丢失）。"""
    chunks = [
        _chunk("a:1", "### 2.2 商品子模块1\n" + _BODY),
        _chunk("a:2", "### 2.3 商品子模块2\n" + _BODY),
        _chunk("a:3", "### 2.4 商品子模块3\n" + _BODY),
    ]
    kept = ChunkingDedupMixin._deduplicate_chunks(chunks)
    assert len(kept) == 3


def test_anchorless_chunks_still_deduped():
    """无标题正文（如汇总段）不受锚点规则影响，近似重复照常合并。"""
    long_text = _BODY
    kept = ChunkingDedupMixin._deduplicate_chunks(
        [_chunk("a:1", long_text + "尾部甲"), _chunk("a:2", long_text + "尾部乙")]
    )
    assert len(kept) == 1
