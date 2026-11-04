# -*- coding: utf-8 -*-
"""
追溯链路引擎 — 数据模型
======================
定义 TraceLink（单条需求追溯链路）和 CoverageReport（覆盖率报告）两个核心 dataclass。
"""

from dataclasses import dataclass, field


@dataclass
class TraceLink:
    """单条需求的追溯链路。"""

    req_id: str
    module: str = ""
    description: str = ""
    # ★ 07-06 单一事实源：api/db/figma 资源详情不再持久化到 trace_links 表，
    #   而是走 trace_link_chunks + rag_chunks。以下字段仅作为内存展示/计算缓存。
    api_endpoints: list[dict] = field(default_factory=list)  # [{method, path, summary}]
    db_tables: dict[str, list[str]] = field(
        default_factory=dict
    )  # {table: [col1, col2]}
    figma_pages: list[str] = field(default_factory=list)  # ["页面名1", "页面名2"]
    chunk_links: list[dict] = field(
        default_factory=list
    )  # [{chunk_type, chunk_id, rel_type, confidence}]
    related_reqs: list[str] = field(default_factory=list)  # ["REQ-003"]
    confidence: str = "medium"  # high | medium | low
    feature_id: int = 0  # 关联功能清单ID → system_feature_list.id（0=未关联）


@dataclass
class CoverageReport:
    """数据源覆盖率报告。"""

    api_coverage: dict = field(
        default_factory=dict
    )  # {endpoint_key: {covered, case_ids}}
    db_coverage: dict = field(default_factory=dict)  # {table_name: {covered, case_ids}}
    page_coverage: dict = field(
        default_factory=dict
    )  # {page_name: {covered, case_ids}}
    trace_completeness: dict = field(
        default_factory=dict
    )  # {req_id: {chain_complete, missing_links}}
    summary: dict = field(default_factory=dict)
