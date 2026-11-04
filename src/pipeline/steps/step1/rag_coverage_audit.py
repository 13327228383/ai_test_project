# -*- coding: utf-8 -*-
"""Step1 上传文档的 RAG 切片覆盖审计（构建后自检）。

不变量：上传文档正文必须逐字进入 rag_chunks。切片链本身带多级降级
（大纲 → 自然边界 → 硬切；excel/csv → 表格行拆分），任一分支退化都只体现为
"chunk 数变少"，无法证明内容仍在。本模块在建索引后直接比对持久化事实
（source_documents.content vs rag_chunks.text），不依赖构建期内存态。

缺失口径：按"去重容忍的逐行存在性"判定 —— 正文每个非空行必须在切片集合
（空白折叠后拼接）中出现至少一次。RAG 构建对重复内容做精确/语义去重，
重复段落只保留一份，按字符多重集计数会把"正常去重"误判为丢失；按行存在性
判定只关心"这段内容还在不在"。char_ratio 仍按字符多重集记录，仅供诊断。
"""

import logging

from src.core.upload_documents import fetch_uploaded_documents

_log = logging.getLogger(__name__)

# 缺失样例条数上限（写入 sources_status，避免撑爆状态 JSON）
_MISSING_SAMPLE_LIMIT = 3
_SAMPLE_TEXT_LIMIT = 60


def _collapse(text) -> str:
    """去掉全部空白（换行/空格差异不构成内容丢失）。"""
    return "".join(str(text or "").split())


def _char_counts(text: str) -> dict:
    counts: dict = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    return counts


def audit_document(source_text: str, chunk_texts: list[str]) -> dict:
    """单文档覆盖审计 → {chars, chunk_chars, ratio, char_ratio, missing_lines, missing}。"""
    src = _collapse(source_text)
    joined = _collapse("\n".join(chunk_texts))
    if not src:
        return {
            "chars": 0,
            "chunk_chars": len(joined),
            "ratio": 1.0,
            "char_ratio": 1.0,
            "missing_lines": 0,
            "missing": [],
        }

    chunk_counts = _char_counts(joined)
    covered = sum(
        min(count, chunk_counts.get(ch, 0)) for ch, count in _char_counts(src).items()
    )

    missing: list[str] = []
    missing_count = 0
    missing_chars = 0
    seen: set = set()
    for line in str(source_text).split("\n"):
        key = _collapse(line)
        if not key or key in seen:
            continue
        seen.add(key)
        if key in joined:
            continue
        missing_count += 1
        missing_chars += len(key)
        if len(missing) < _MISSING_SAMPLE_LIMIT:
            missing.append(line.strip()[:_SAMPLE_TEXT_LIMIT])

    distinct_chars = sum(len(k) for k in seen) or 1
    ratio = 1.0 if not missing_chars else round(1 - missing_chars / distinct_chars, 4)
    return {
        "chars": len(src),
        "chunk_chars": len(joined),
        "ratio": ratio,
        "char_ratio": round(covered / len(src), 4),
        "missing_lines": missing_count,
        "missing": missing,
    }


def _load_chunk_texts(
    conn, project: str, doc_type: str, version_label: str
) -> list[str]:
    """读取某上传文档行（doc_type 为行身份）在当前版本分区的切片文本。"""
    rows = conn.execute(
        "SELECT text FROM rag_chunks WHERE scope_id = %s AND version_label = %s "
        "AND chunk_id LIKE %s AND is_active = 1",
        (project, version_label, "upload:%s:%%" % doc_type),
    ).fetchall()
    return [r.get("text", "") or "" for r in (rows or [])]


def audit_upload_coverage(project: str, version_label: str) -> dict:
    """审计项目 + 版本内全部上传文档的切片覆盖。

    返回 {status, detail, documents}；status ∈ ok / failed / skipped。
    零切片或存在丢失段落 = failed（正文没全进索引，下游只能基于残缺需求推理）。
    """
    if not project or not version_label:
        return {"status": "skipped", "detail": "无项目/版本上下文", "documents": []}

    conn = None
    try:
        from src.core.db_manager import get_connection

        conn = get_connection()
        rows = fetch_uploaded_documents(project, version_label)
        if not rows:
            return {"status": "skipped", "detail": "无上传文档", "documents": []}

        documents = []
        for row in rows:
            doc_type = row.get("doc_type") or ""
            content = row.get("content") or ""
            chunk_texts = _load_chunk_texts(conn, project, doc_type, version_label)
            result = audit_document(content, chunk_texts)
            result.update(
                {
                    "doc_type": doc_type,
                    "filename": row.get("filename", ""),
                    "format": row.get("file_format", ""),
                    "category": row.get("category", ""),
                    "chunks": len(chunk_texts),
                }
            )
            documents.append(result)

        lost = [
            d
            for d in documents
            if d["chars"] > 0 and (d["chunks"] == 0 or d["missing_lines"] > 0)
        ]
        status = "failed" if lost else "ok"
        detail = (
            "全部正文已入索引（%d 份上传文档）" % len(documents)
            if status == "ok"
            else "%d/%d 份上传文档切片不完整" % (len(lost), len(documents))
        )
        if status != "ok":
            _log.error(
                "[RAG覆盖审计] %s：%s",
                detail,
                [
                    {
                        "filename": d["filename"],
                        "chunks": d["chunks"],
                        "missing_lines": d["missing_lines"],
                        "missing": d["missing"],
                    }
                    for d in lost
                ],
            )
        else:
            _log.info("[RAG覆盖审计] %s", detail)
        return {"status": status, "detail": detail, "documents": documents}
    except Exception as e:
        _log.error("[RAG覆盖审计] 执行失败: %s", str(e)[:120], exc_info=True)
        return {"status": "failed", "detail": "审计异常: %s" % str(e)[:80], "documents": []}
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _pass_e:
                _log.debug("连接关闭异常: %s", _pass_e)
