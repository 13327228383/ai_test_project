# -*- coding: utf-8 -*-
"""
smart_figma_fetcher 的 Layer4 格式化输出子模块
===============================================
零业务逻辑变更，仅做物理代码搬迁。
"""

from typing import Any

from src.pipeline.steps.step1._smart_fetcher_utils import extract_elements_flat
from src.pipeline.steps.step1.figma_parser import parse_figma_document


def format_smart_output(l3_result: dict, l1_result: dict) -> dict[str, Any]:
    """
    格式化输出：生成含变更标记的 UI 描述文本。
    变更的页面打 ⚠️ 前缀，新增的页面打 ✨ 前缀。
    """
    nodes = l3_result.get("nodes", {})
    changed = set(l3_result.get("changed_ids", []))
    new = set(l3_result.get("new_ids", []))
    unchanged = set(l3_result.get("unchanged_ids", []))

    change_lines = []
    l1_pages = {p["id"]: p for p in l1_result.get("pages", [])}

    if new:
        change_lines.append("## ✨ 新增页面 (%d):" % len(new))
        for nid in new:
            name = l1_pages.get(nid, {}).get("name", nid)
            change_lines.append("  - ✨ %s" % name)
        change_lines.append("")

    if changed:
        change_lines.append("## ⚠️ 变更页面 (%d) — 请重点关注:" % len(changed))
        for nid in changed:
            name = l1_pages.get(nid, {}).get("name", nid)
            change_lines.append("  - ⚠️ %s" % name)
        change_lines.append("")

    if unchanged:
        change_lines.append("## ✓ 未变更页面 (%d):" % len(unchanged))
        for nid in list(unchanged)[:10]:
            name = l1_pages.get(nid, {}).get("name", nid)
            change_lines.append("  - %s" % name)
        if len(unchanged) > 10:
            change_lines.append("  - ... 等 %d 个" % (len(unchanged) - 10))
        change_lines.append("")

    change_summary = (
        "\n".join(change_lines) if change_lines else "（无设计稿变更检测数据）"
    )

    parts = []

    file_name = l1_result.get("file_name", "")
    parts.append("## Figma 设计稿: %s" % file_name)
    parts.append("")

    if changed or new:
        parts.append("### ⚠️ 设计稿变更提示（请重点关注以下页面）")
        for nid in new:
            name = l1_pages.get(nid, {}).get("name", nid)
            parts.append("- ✨ [新增页面] %s — 之前未拉取过，请全面分析" % name)
        for nid in changed:
            name = l1_pages.get(nid, {}).get("name", nid)
            parts.append("- ⚠️ [变更页面] %s — 刚刚发生更新，请重点关注变更内容" % name)
        parts.append("")

    for nid, node_info in nodes.items():
        page_name = l1_pages.get(nid, {}).get("name", nid)
        canvas = l1_pages.get(nid, {}).get("canvas", "")

        prefix = ""
        if nid in new:
            prefix = "✨ [新增] "
        elif nid in changed:
            prefix = "⚠️ [已变更] "

        parts.append("### %s%s" % (prefix, page_name))
        if canvas:
            parts.append("  所属画布: %s" % canvas)

        document = node_info.get("document") if isinstance(node_info, dict) else None
        # ★ v0.13.2 simplified 节点无 document 键，但节点本身含 children 树，
        # 可直接作 document 传入 parse_figma_document（_collect_frames 仅读 type/name/children）。
        if not document and isinstance(node_info, dict) and node_info.get("children"):
            document = node_info
        if document:
            ui_desc = parse_figma_document(
                document
            )  # ★ Fix55a: 不传 max_depth，走 config.figma_parse_max_depth
            if ui_desc:
                parts.append(ui_desc)
            else:
                elements = extract_elements_flat(document)
                if elements:
                    parts.append("- 关键元素: %s" % ", ".join(elements[:30]))
        parts.append("")

    figma_text = "\n".join(parts)

    return {
        "figma_text": figma_text,
        "change_summary": change_summary,
        "changed_pages": [
            {"id": nid, "name": l1_pages.get(nid, {}).get("name", nid)}
            for nid in changed
        ],
        "new_pages": [
            {"id": nid, "name": l1_pages.get(nid, {}).get("name", nid)} for nid in new
        ],
        "total_pages": len(nodes),
    }
