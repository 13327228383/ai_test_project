# -*- coding: utf-8 -*-
from __future__ import annotations

"""
parser._section_parser — Markdown 章节解析
===========================================
parse_markdown_sections, detect_transfer_scope_section
"""

import re

from ._models import SECTION_DETECTION_THRESHOLD


def parse_markdown_sections(text: str) -> list[dict]:
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
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
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
                    "heading": "",
                    "level": 0,
                    "body": preamble,
                    "start": 0,
                },
            )
    return sections


def detect_transfer_scope_section(sections: list[dict]) -> dict | None:
    """
    用评分机制找到"转测范围"章节。
    不受编号（9. / 8. / 无编号）、标题级别（h2/h3）变化影响。
    """
    best_sec = None
    best_score = 0

    for sec in sections:
        heading = sec.get("heading", "")
        body = sec.get("body", "")
        level = sec.get("level", 0)
        score = 0

        # ── 标题信号 ──
        hl = heading.lower().replace(" ", "")
        if "测试范围" in hl:
            score += 40
        elif "转测" in hl and "范围" in hl:
            score += 35
        elif "转测" in hl:
            score += 30
        elif "范围" in hl:
            score += 20
        if "版本" in hl:
            score += 10

        # ── 标题级别信号（h2/h3 更可能是主要章节） ──
        if level in (2, 3):
            score += 5

        # ── body 内容信号 ──
        if "|" in body:
            # 检查是否含有关键列表头
            table_header_signals = 0
            if re.search(r"\|\s*模块", body):
                table_header_signals += 1
            if re.search(r"\|\s*功能点", body):
                table_header_signals += 1
            if re.search(r"转测版本|版本号", body):
                table_header_signals += 1
            score += table_header_signals * 10

        if re.search(r"V\d+\.\d+\.\d+", body):
            score += 8
        if re.search(r"转测", body):
            score += 8

        if score > best_score:
            best_score = score
            best_sec = sec

    if best_score >= SECTION_DETECTION_THRESHOLD:
        return best_sec

    # ── 后备策略：全文搜索第一个含"模块|功能点"的表格 ──
    if sections:
        full_body = "\n".join(s.get("body", "") for s in sections)
        if re.search(r"\|\s*模块\s*\|.*\|\s*功能点\s*\|", full_body):
            for sec in sections:
                if "|" in sec.get("body", "") and re.search(
                    r"\|\s*模块", sec.get("body", "")
                ):
                    return sec

    return None
