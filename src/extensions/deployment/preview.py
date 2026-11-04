# -*- coding: utf-8 -*-
"""
部署文档 预览模块
================
轻量预览：解析文档中的转测范围表格，返回版本列表和功能点概览。
不做语义匹配，不需要 embedding。前端用它展示版本下拉框。
"""

from typing import Any

from src.extensions.deployment.parser import (
    parse_markdown_sections,
    detect_transfer_scope_section,
    parse_table,
)


def preview_versions(doc_text: str) -> dict[str, Any]:
    """轻量预览：解析文档中的转测范围表格，返回版本列表和功能点概览。
    不做语义匹配，不需要 embedding。前端用它展示版本下拉框。"""
    # 解析章节
    sections = parse_markdown_sections(doc_text)
    if not sections:
        return {"versions": [], "features": [], "error": "文档无章节结构"}

    # 检测转测范围章节
    scope_section = detect_transfer_scope_section(sections)
    if not scope_section:
        return {"versions": [], "features": [], "error": "未检测到转测范围章节"}

    # 解析表格
    rows = parse_table(scope_section.get("body", ""))
    if not rows:
        return {"versions": [], "features": [], "error": "未解析到表格数据"}

    # 提取版本列表
    versions = sorted(set(x for r in rows if (x := r.get("version", "").strip())))

    # 提取功能点概览（模块 + 功能点 + 版本）
    features = []
    for r in rows:
        v = r.get("version", "").strip()
        m = r.get("module", "").strip()
        f = r.get("feature", r.get("notes", "")).strip()
        if m or f:
            features.append({"module": m, "feature": f, "version": v})

    return {
        "versions": versions,
        "features": features,
        "total": len(features),
        "section_heading": scope_section.get("heading", "(未知)"),
    }
