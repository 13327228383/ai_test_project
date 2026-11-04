# -*- coding: utf-8 -*-
"""
scheduler_helpers.py — 从 scheduler.py 提取的独立静态工具函数
============================================================
包含文档解析、关键词提取、模块上下文构建、Figma 页面匹配、
API 测试用例判断等纯函数，供 PipelineScheduler 调用。
"""

import re
import logging

_log = logging.getLogger(__name__)

# ★ 模块常量：替代魔法数字（与 scheduler.py 保持一致）
_KEYWORD_MIN_MATCH_RATIO = 2  # 关键词最小匹配数（模块-段落关联度校验）
_KEYWORD_SINGLE_FALLBACK = 1  # 仅1个关键词时的最小匹配数
_FIGMA_CONTEXT_MAX_CHARS = 3000  # Figma UI 上下文最大截断字符数


# =====================================================================
# 文档解析
# =====================================================================


def parse_doc_sections(text: str) -> list[dict]:
    """按 Markdown 标题解析文档为段落树。"""
    sections = []
    pattern = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return [{"heading": "", "level": 0, "body": text, "start": 0}]

    for i, m in enumerate(matches):
        level = len(m.group(1))
        heading = m.group(2).strip()
        start = m.start()
        body_start = m.end()
        if i + 1 < len(matches):
            body_end = matches[i + 1].start()
        else:
            body_end = len(text)
        body = text[body_start:body_end].strip()
        sections.append(
            {
                "heading": heading,
                "level": level,
                "body": body,
                "start": start,
            }
        )

    if matches and matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.insert(
                0,
                {
                    "heading": "前言/概述",
                    "level": 0,
                    "body": preamble,
                    "start": 0,
                },
            )

    return sections


def build_skeleton(sections: list[dict]) -> str:
    """构建文档骨架（目录结构）。"""
    lines = ["## 需求文档目录结构", ""]
    for i, sec in enumerate(sections):
        indent = "  " * max(0, sec["level"] - 1)
        prefix = "#" * sec["level"] if sec["level"] > 0 else ""
        label = "%s %s" % (prefix, sec["heading"]) if prefix else sec["heading"]
        lines.append("%s%d. %s (%d 字符)" % (indent, i, label, len(sec["body"])))
    lines.append("")
    return "\n".join(lines)


# =====================================================================
# 关键词提取与匹配
# =====================================================================


def extract_keywords(text: str) -> list[str]:
    """从文本中提取用于匹配的关键词列表。
    对长中文词同时拆分为2字片段，提升短词匹配率（如「角色权限管理」→ [角色权限管理, 角色, 权限, 管理]）。"""
    full = [kw for kw in re.findall(r"[一-鿿\w]{2,}", text) if len(kw) >= 2]
    # 对 >=4 字的纯中文词，额外拆出 2 字滑动窗口
    extra = []
    for kw in full:
        if len(kw) >= 4 and re.match(r"^[一-鿿]+$", kw):
            for i in range(len(kw) - 1):
                extra.append(kw[i : i + 2])
    return full + extra


def validate_module_section_match(mod_name: str, matched: list[dict]) -> bool:
    """校验模块名与匹配段落的关联度（同时搜索标题和正文）。"""
    if not matched:
        return False
    keywords = extract_keywords(mod_name)
    if not keywords:
        return True
    min_match = min(_KEYWORD_MIN_MATCH_RATIO, len(keywords) // 2 or 1)
    for sec in matched:
        search_text = " ".join(
            [
                sec.get("heading", ""),
                sec.get("body", ""),
            ]
        )
        hits = sum(1 for kw in keywords if kw in search_text)
        if hits >= min_match:
            return True
    return False


# =====================================================================
# 模块上下文构建
# =====================================================================


def build_module_context(
    mod_name: str, section_index: int, sections: list[dict], skeleton: str
) -> str:
    """为单个模块构建带骨架和溯源信息的上下文文本。"""
    matched = []
    fallback_reason = ""
    keywords = extract_keywords(mod_name)
    kw_display = ", ".join(keywords[:6]) + ("..." if len(keywords) > 6 else "")

    if 0 <= section_index < len(sections):
        candidate = [sections[section_index]]
        if validate_module_section_match(mod_name, candidate):
            matched = candidate
            _log.info(
                "[OK] %s → 段落[%d] %s (关键词: %s)",
                mod_name,
                section_index,
                sections[section_index].get("heading", "?")[:30],
                kw_display,
            )
        else:
            sec_heading = sections[section_index].get("heading", "?")[:30]
            # 显示在标题+正文中各自命中了几个关键词
            search_text = " ".join(
                [sec_heading, sections[section_index].get("body", "")]
            )
            hit_kws = [kw for kw in keywords if kw in search_text]
            fallback_reason = (
                "段落[%d]「%s」正文不包含关键词 (命中%d/%d: %s)，尝试全文档搜索"
                % (
                    section_index,
                    sec_heading,
                    len(hit_kws),
                    min(_KEYWORD_MIN_MATCH_RATIO, len(keywords) // 2 or 1),
                    ", ".join(hit_kws) if hit_kws else "无命中",
                )
            )

    if not matched:
        if fallback_reason:
            _log.warning("[WARN] %s (%s)", mod_name, fallback_reason)
        for sec in sections:
            search_text = " ".join([sec.get("heading", ""), sec.get("body", "")])
            hits = sum(1 for kw in keywords if kw in search_text)
            if hits >= min(_KEYWORD_MIN_MATCH_RATIO, len(keywords) // 2 or 1):
                matched.append(sec)
                _log.warning(
                    "→ 回退匹配到段落「%s」(%d/%d 关键词命中)",
                    sec.get("heading", "?")[:30],
                    hits,
                    len(keywords),
                )

    if not matched and sections:
        mid = len(sections) // 2
        matched = [sections[min(mid, len(sections) - 1)]]
        sec_heading = matched[0].get("heading", "?")[:30]
        _log.warning(
            "%s → 全文档搜索无结果 (关键词=%s)，兜底用中段段落[%d]「%s」",
            mod_name,
            kw_display,
            mid,
            sec_heading,
        )

    parts = [skeleton]
    parts.append("> 📍 当前模块: **%s**" % mod_name)
    for sec in matched:
        sec_id = "§%s" % sec["heading"] if sec["heading"] else "§前言"
        parts.append("\n## 来源段落: %s\n" % sec_id)
        parts.append(sec["body"])
    parts.append("\n---\n> ⚠️ 请仅拆解「%s」模块的需求，忽略其他模块内容。" % mod_name)

    return "\n".join(parts)


# =====================================================================
# Figma 页面匹配
# =====================================================================


def match_figma_pages(ui_ctx: str, nav_ctx: str, module_name: str) -> str:
    """从 UI 描述和导航地图中匹配与模块相关的 Figma 页面。"""
    if not ui_ctx:
        return ""
    keywords = [kw for kw in re.findall(r"[一-鿿\w]{2,}", module_name) if len(kw) >= 2]
    if not keywords:
        return ""
    min_match = (
        _KEYWORD_SINGLE_FALLBACK if len(keywords) == 1 else _KEYWORD_MIN_MATCH_RATIO
    )
    parts = []
    for section in ui_ctx.split("### "):
        hits = sum(1 for kw in keywords if kw in section)
        if hits >= min_match or (min_match == 1 and hits >= 1):
            parts.append(section)
    if parts:
        return "### " + "\n### ".join(parts)[:_FIGMA_CONTEXT_MAX_CHARS]
    return ""


# =====================================================================
# API 测试用例判断
# =====================================================================


def is_api_only_intent(intent: dict) -> bool:
    """判断测试用例是否可以走 API 直调快速通道（无需浏览器）。

    v9 简化：只看 required_tracks。不含 "ui" → api_only。无 required_tracks → 安全兜底 NOT api_only。
    """
    if not isinstance(intent, dict):
        return False

    payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
    required_tracks = (
        intent.get("required_tracks") or payload.get("required_tracks") or []
    )
    if isinstance(required_tracks, str):
        try:
            import json as _json

            required_tracks = _json.loads(required_tracks)
        except Exception as _e:
            _log.debug(
                "[scheduler_helpers] is_api_only_intent 异常: %s", _e, exc_info=True
            )
            required_tracks = [required_tracks]

    if required_tracks:
        return "ui" not in [str(t).lower().strip() for t in required_tracks]

    return False
