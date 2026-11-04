# -*- coding: utf-8 -*-
"""★ P2-11 接入主流程: MultiModalRAG 索引/检索集成层。

定位：
  提供 Step1 数据索引（写入 multimodal_chunks）和 Step3 检索（注入 prompt）的统一入口。
  解耦 rag/store.py+retriever.py 与 Step1/Step3 主流程，避免主流程直接操作 rag 内部接口。

职责边界（§13）：
  - Step1 拉取数据后 → 调用 index_* 写入 multimodal_chunks
  - Step3 生成用例前 → 调用 retrieve_context 检索相关内容注入 prompt
  - 本模块只做"格式转换 + 调用 rag store/retriever"，不含业务逻辑

通用性（§12.4）：
  1. 跨系统：索引/检索用通用字段（project/version_label/source_type/ref_id/content）
  2. 跨行业：任何需求文档/DOM/figma 都能索引
  3. 降级：rag store/retriever 异常 → 返回空，不阻断主流程
  4. 职责边界：只做索引/检索，不做决策
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.core.versioning import current_version_id

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 索引侧（Step1 调用）
# ═══════════════════════════════════════════════════════════════


def index_dom_snapshots(
    project: str,
    version_label: str,
    snapshots: list[dict[str, Any]],
) -> int:
    """将 DOM 快照索引到 multimodal_chunks（source_type='dom'）。

    在 Step1 save_dom_snapshots 完成后调用。
    通用性：content = page_title + dom_summary，不含业务术语。
    降级：异常返回 0，不阻断主流程。

    返回: 索引的 chunk 数。
    """
    if not project or not snapshots:
        return 0
    try:
        from src.rag.store import get_store

        store = get_store()
        # 清理旧 DOM 索引（同 project 同活跃版本，避免重复累积）
        store.delete_chunks(project, source_type="dom")
        vid = int(current_version_id(project) or 0)
        count = 0
        for snap in snapshots:
            page_url = snap.get("page_url", "") or ""
            page_title = snap.get("page_title", "") or ""
            dom_summary = snap.get("dom_summary", "") or ""
            if not page_url or not dom_summary:
                continue
            content = page_title + "\n" + dom_summary if page_title else dom_summary
            cid = store.add_chunk(project, "dom", page_url, content, version_id=vid)
            if cid > 0:
                count += 1
        _log.info("[RAGInteg] ✓ DOM 索引: %d chunks (project=%s)", count, project)
        return count
    except Exception as _e:
        _log.warning("[RAGInteg] DOM 索引失败(非阻断): %s", str(_e)[:120])
        return 0


def index_requirements(
    project: str,
    version_label: str,
    requirements: list[dict[str, Any]],
) -> int:
    """将需求文档索引到 multimodal_chunks（source_type='requirement'）。

    在 Step2 需求拆解完成后调用（需求三元组作为 chunk）。
    通用性：content = operation + object + expected，ref_id = req_id。
    降级：异常返回 0，不阻断主流程。

    返回: 索引的 chunk 数。
    """
    if not project or not requirements:
        return 0
    try:
        from src.rag.store import get_store

        store = get_store()
        store.delete_chunks(project, source_type="requirement")
        vid = int(current_version_id(project) or 0)
        count = 0
        for req in requirements:
            req_id = str(req.get("req_id", "") or req.get("id", "") or "")
            operation = str(req.get("operation", "") or "").strip()
            obj = str(req.get("object", "") or req.get("target", "") or "").strip()
            expected = str(
                req.get("expected", "") or req.get("expected_feedback", "") or ""
            ).strip()
            content = " | ".join(filter(None, [operation, obj, expected]))
            if not content:
                continue
            cid = store.add_chunk(
                project, "requirement", req_id, content, version_id=vid
            )
            if cid > 0:
                count += 1
        _log.info("[RAGInteg] ✓ 需求索引: %d chunks (project=%s)", count, project)
        return count
    except Exception as _e:
        _log.warning("[RAGInteg] 需求索引失败(非阻断): %s", str(_e)[:120])
        return 0


def index_figma(
    project: str,
    version_label: str,
    figma_nodes: list[dict[str, Any]],
) -> int:
    """将 figma 设计稿索引到 multimodal_chunks（source_type='figma'）。

    在 Step1 figma 拉取完成后调用。
    通用性：content = node_name + node_text，ref_id = node_id。
    降级：异常返回 0，不阻断主流程。

    返回: 索引的 chunk 数。
    """
    if not project or not figma_nodes:
        return 0
    try:
        from src.rag.store import get_store

        store = get_store()
        store.delete_chunks(project, source_type="figma")
        vid = int(current_version_id(project) or 0)
        count = 0
        for node in figma_nodes:
            node_id = str(node.get("id", "") or node.get("node_id", "") or "")
            name = str(node.get("name", "") or node.get("text", "") or "").strip()
            if not name:
                continue
            cid = store.add_chunk(project, "figma", node_id, name, version_id=vid)
            if cid > 0:
                count += 1
        _log.info("[RAGInteg] ✓ Figma 索引: %d chunks (project=%s)", count, project)
        return count
    except Exception as _e:
        _log.warning("[RAGInteg] Figma 索引失败(非阻断): %s", str(_e)[:120])
        return 0


# ═══════════════════════════════════════════════════════════════
# 检索侧（Step3 调用）
# ═══════════════════════════════════════════════════════════════


def retrieve_context(
    project: str,
    query: str,
    top_k: int = 5,
    source_types: Optional[list[str]] = None,
) -> str:
    """检索 multimodal_chunks，返回格式化的上下文文本（注入 Step3 prompt）。

    通用性：query 是需求关键词（operation+object），不含业务术语。
    降级：检索失败或无结果 → 返回空串，不阻断主流程。

    参数:
      project: 项目名
      query: 检索关键词（如 "新建配方"）
      top_k: 返回 top K 结果
      source_types: 限定来源类型（None=全部，["dom","requirement"]=限定）

    返回: 格式化的上下文文本（空串=无结果）。
    """
    if not project or not query or not query.strip():
        return ""
    try:
        from src.rag.retriever import get_retriever

        retriever = get_retriever()

        if source_types:
            # 跨模态检索（限定来源）
            results = []
            for st in source_types:
                partial = retriever.retrieve_by_source(project, query, st, top_k=top_k)
                results.extend(partial)
            # 按 score 降序
            results.sort(key=lambda r: r.get("score", 0), reverse=True)
            results = results[:top_k]
        else:
            results = retriever.retrieve(project, query, top_k=top_k)

        if not results:
            return ""

        lines = []
        for r in results:
            st = r.get("source_type", "")
            content = (r.get("content", "") or "")[:200]
            score = r.get("score", 0)
            ref = r.get("ref_id", "")
            label = {"requirement": "需求", "dom": "DOM", "figma": "设计"}.get(st, st)
            lines.append(
                "  - [%s] %s (score=%.2f, ref=%s)"
                % (label, content, score, str(ref)[:30])
            )

        _ctx = (
            "\n## ★ RAG 检索结果（相关上下文，防止编造）\n"
            "以下是与当前需求语义相关的需求/DOM/设计稿内容（来自 multimodal_chunks 检索）。\n"
            "生成 actions.checks 时，expected 必须参考以下内容，禁止编造与下方不符的元素/接口/字段：\n"
            "%s\n"
        ) % "\n".join(lines)
        _log.info(
            "[RAGInteg] ✓ RAG 检索: query='%s' → %d 结果", str(query)[:30], len(results)
        )
        return _ctx
    except Exception as _e:
        _log.warning("[RAGInteg] RAG 检索失败(非阻断): %s", str(_e)[:120])
        return ""


def get_stats(project: str) -> dict[str, int]:
    """获取 multimodal_chunks 索引统计（用于诊断）。"""
    try:
        from src.rag.store import get_store

        return get_store().get_stats(project)
    except Exception as _e:
        _log.debug("[RAGInteg] get_stats 失败(降级返回空): %s", str(_e)[:100])
        return {}
