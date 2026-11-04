# -*- coding: utf-8 -*-
"""Step 2 覆盖率校验函数。
从 step2.py 拆分出。★ v4.0 (2026-07-12): 适配切片驱动架构，接入 feature_driven_breakdown 实时计算。"""

import logging

_log = logging.getLogger(__name__)

# ── 命名常量 ──
_SIGNIFICANT_SECTION_MIN_CHARS = 50  # 重要章节最小正文长度（字符）
_HEADING_PREFIX_MATCH_LEN = 15  # 标题模糊匹配的前缀长度
_MODULE_BODY_SEARCH_LEN = 200  # 模块关键词搜索的正文截断长度
_LOG_LIST_MAX_SHOW = 5  # 列表类日志最大显示条数


def run_bidirectional_coverage_check(
    all_reqs: list, chunks: list, covered_ids: set = None
) -> dict:
    """
    拆解完成后自动比对（适配切片驱动架构 v4.0）：

    a) 前向覆盖：原始文档的所有重要章节 → 是否都有需求覆盖
       - 以 chunks 的 section（章节标题）为文档章节
       - 以 covered_ids 为已覆盖切片集合
       - 已覆盖 = 该章节下至少有一个切片被提取了需求

    b) 反向溯源：拆解出的所有需求 → 是否都能追溯到文档切片
       - 通过 req.slice_id / req.source_quote 验证

    覆盖率 < 100% 时打印告警，但不阻塞流程。

    Returns:
        {"forward_rate": float, "uncovered_sections": int, "orphan_reqs": int,
         "uncovered_section_names": list[str], "orphan_req_details": list[str]}
    """
    import re as _re

    if not chunks or not all_reqs:
        _log.info("[Coverage] 跳过双向覆盖检查（无切片或无需求）")
        return {
            "forward_rate": 0,
            "uncovered_sections": 0,
            "orphan_reqs": 0,
            "uncovered_section_names": [],
            "orphan_req_details": [],
        }

    covered_ids = covered_ids or set()

    # ═══════════════════════════════════════════
    # 前向覆盖：文档章节 → 切片覆盖
    # ═══════════════════════════════════════════
    # 按 section 分组 chunks，统计每个章节的覆盖情况
    section_chunks: dict[str, list[dict]] = {}
    for c in chunks:
        section = c.get("section", "") or "__未分类__"
        section_chunks.setdefault(section, []).append(c)

    significant_sections = [
        (sec, clist)
        for sec, clist in section_chunks.items()
        if sec != "__未分类__"
        and sec.strip()
        and sum(len(c.get("text", "")) for c in clist) > _SIGNIFICANT_SECTION_MIN_CHARS
    ]

    uncovered_sections = []
    for sec_name, clist in significant_sections:
        sec_covered = any(c["chunk_id"] in covered_ids for c in clist)
        if not sec_covered:
            uncovered_sections.append(sec_name[:40])

    total_significant = (
        len(significant_sections) if significant_sections else len(section_chunks)
    )
    forward_rate = round(
        (total_significant - len(uncovered_sections)) / max(total_significant, 1) * 100,
        2,
    )

    # ═══════════════════════════════════════════
    # 反向溯源：需求 → 文档切片
    # ═══════════════════════════════════════════
    orphan_reqs = []
    # 构建 chunk_id → chunk 索引
    chunk_index = {c["chunk_id"]: c for c in chunks}

    for req in all_reqs:
        slice_id = req.get("slice_id", "")
        source_quote = req.get("source_quote", "")
        module = req.get("module", "")
        operation = req.get("operation", "")

        # 检查 slice_id 是否有效
        if slice_id and slice_id in chunk_index:
            continue  # 有效的切片引用

        # 检查 source_quote 是否能在任何切片中找到
        if source_quote:
            found_in_chunk = False
            for c in chunks:
                if source_quote[:30] in c.get("text", ""):
                    found_in_chunk = True
                    break
            if found_in_chunk:
                continue

        # 孤儿需求：既无有效 slice_id，source_quote 也无法在切片中定位
        orphan_reqs.append(
            f"{operation[:20]}(模块:{module[:15]}, slice:{slice_id[:20]})"
        )

    # ═══════════════════════════════════════════
    # 输出结果
    # ═══════════════════════════════════════════
    if uncovered_sections:
        _log.warning(
            "[Coverage] 前向覆盖缺口: %d/%d 个重要章节未被任何需求覆盖 — %s",
            len(uncovered_sections),
            total_significant,
            ", ".join(uncovered_sections[:_LOG_LIST_MAX_SHOW])
            + ("..." if len(uncovered_sections) > _LOG_LIST_MAX_SHOW else ""),
        )

    if orphan_reqs:
        _log.warning(
            "[Coverage] 反向溯源异常: %d 个需求无法追溯到文档切片 — %s",
            len(orphan_reqs),
            ", ".join(orphan_reqs[:_LOG_LIST_MAX_SHOW])
            + ("..." if len(orphan_reqs) > _LOG_LIST_MAX_SHOW else ""),
        )

    if forward_rate < 100:
        _log.warning("[Coverage] 双向覆盖率: 前向 %.1f%% (阈值100%%)", forward_rate)
    else:
        _log.info(
            "[Coverage] 双向覆盖率: 前向 100%% (%d/%d 章节已覆盖)",
            total_significant - len(uncovered_sections),
            total_significant,
        )

    # ★ 指标写入由 step2.py 统一负责（避免重复写入），此处仅返回计算结果
    return {
        "forward_rate": forward_rate,
        "uncovered_sections": len(uncovered_sections),
        "orphan_reqs": len(orphan_reqs),
        "uncovered_section_names": uncovered_sections,
        "orphan_req_details": orphan_reqs,
    }


# ★ 保留旧签名兼容（已被 run_bidirectional_coverage_check 替代）
def _run_bidirectional_coverage_check(
    all_reqs: list, modules: list, doc_sections: list
) -> None:
    """旧接口兼容适配器。将 modules/doc_sections 转换为 chunks 格式后调用新函数。"""
    _log.debug(
        "[Coverage] 使用旧接口适配器，建议迁移到 run_bidirectional_coverage_check(chunks=...)"
    )
    chunks = [
        {
            "chunk_id": f"sec_{i}",
            "text": s.get("body", ""),
            "section": s.get("heading", ""),
        }
        for i, s in enumerate(doc_sections)
    ]
    # 从 modules 中提取已覆盖的 section_index
    covered_indices = {
        m.get("section_index", -1) for m in modules if m.get("section_index", -1) >= 0
    }
    covered_ids = {f"sec_{i}" for i in covered_indices if i < len(doc_sections)}
    run_bidirectional_coverage_check(all_reqs, chunks, covered_ids)
