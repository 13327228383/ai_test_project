# -*- coding: utf-8 -*-
"""Step2 预处理层：文本清洗、表格提取、URL 处理、module 净化。

与 feature_driven.py 的协作：
  feature_driven_breakdown() 先调用 preprocess_and_extract_tables(raw_text)
  得到 (cleaned_text, table_chunks)，再对 cleaned_text 做普通切片，
  最后把 table_chunks 作为 type="table" 的独立切片并入。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.pipeline.steps.step2.utils.table_extractor import (
    extract_markdown_tables,
    strip_tables_from_text,
    table_to_text,
    table_to_feature_list,
)

_log = logging.getLogger(__name__)

# ── module 名称清洗 ──
_IMAGE_IN_BRACKETS_RE = re.compile(
    r"\[?\s*(?:图片|图|截图|示意图|流程图|架构图|界面图|原型图|UI图|效果图|界面|原型)\s*[:：]\s*[^\]\n]+\.?(?:png|jpg|jpeg|gif|svg|webp|bmp)\s*\]?",
    re.IGNORECASE,
)
_MD_IMAGE_RE = re.compile(r"!\[.*?\]\(.*?\)")  # Markdown 图片: ![alt](url)
# 仅删除会污染标题的 Markdown 格式符号，保留 [] 作为合法标点的一部分（但单独清理图片语法）
_MD_FORMAT_RE = re.compile(r"[#*_`~!>\-]+")
# 删除全角/半角 Markdown 表格分隔符
_MD_TABLE_SEP_RE = re.compile(r"\|")
_EXTRA_SPACES_RE = re.compile(r"\s+")
_MODULE_MAX_LEN = 60

# ── 标题提取 ──
_HEADING_EXTRACT_RE = re.compile(
    r"(?:^|\n)\s*"
    r"(?:#{1,3}\s+(.+)"
    r"|第[一二三四五六七八九十百0-9]+[章节部分][：:\s]*(.+)"
    r"|[0-9]+\.[0-9.]*\s+(.+)"
    r"|【([^】]+)】\s*"
    r"|\[([^\]]+)\]\s*)",
    re.MULTILINE,
)

# ── hint_text 关键词提取：2-4 字中文 n-gram + 英文单词 + 停用词过滤 ──
_HINT_STOPWORDS = frozenset(
    "的 了 是 在 和 与 或 不 也 就 都 要 会 能 有 这 那 个 为 对 从 到 被 把 "
    "让 使 以 但 而 且 若 如 当 则 应 需 可 须 必 将 已 未 请 只 仅 共 每 此 其 "
    "该 之 于 由 因 所以 什么 如何 怎么 哪些 哪个 多少 请 请 必须 应该 可以 "
    "用户 系统 功能 操作 页面 按钮 列表 接口 表单 数据 字段 提交 输入 选择 "
    "点击 加载 显示 请求 返回 支持 包含".split()
)


def _extract_hint_keywords(hint_text: str) -> list[str]:
    """从 hint_text（operation + condition + source_quote 拼接）提取匹配关键词。

    策略：
    1. 英文单词（≥3 字母）直接提取
    2. 中文文本切 2/3/4 字 n-gram，过停用词
    3. 返回列表按长度降序（长短语优先，更精确）
    """
    if not hint_text or not hint_text.strip():
        return []
    _tokens: list[str] = []
    # 英文单词
    for _w in re.findall(r"[a-zA-Z]{3,}", hint_text):
        _tokens.append(_w.lower())
    # 中文 n-gram (2-4 字)
    _zh = "".join(re.findall(r"[\u4e00-\u9fff]+", hint_text))
    for _n in (4, 3, 2):
        for _i in range(len(_zh) - _n + 1):
            _gram = _zh[_i : _i + _n]
            if _gram in _HINT_STOPWORDS:
                continue
            if all(c in _HINT_STOPWORDS for c in _gram):
                continue
            _tokens.append(_gram)
    # 去重保序，按长度降序
    _seen = set()
    _result: list[str] = []
    for _t in sorted(set(_tokens), key=len, reverse=True):
        if _t not in _seen:
            _seen.add(_t)
            _result.append(_t)
    return _result[:30]  # 上限 30，避免太多噪音

# ── URL 处理 ──
_URL_PATTERN = re.compile(r"https?://\S+")
_PURE_URL_LINE = re.compile(r"^\s*https?://\S+\s*$")
_EMPTY_LINE = re.compile(r"^\s*$")
_PURE_IMAGE_LINE = re.compile(
    r"^\s*(?:"
    r"!\[.*?\]\(.*?\)"  # Markdown 图片
    r"|https?://\S+\.(?:png|jpg|jpeg|gif|svg|webp|bmp)(?:\?\S*)?"  # 图片 URL
    r"|\[?\s*(?:图片|图|截图|示意图|流程图|架构图|界面图|原型图|UI图|效果图|界面|原型)\s*[:：]\s*[^\]]+\.?(?:png|jpg|jpeg|gif|svg|webp|bmp)\s*\]?"  # [图: xxx.png]
    r")\s*$",
    re.IGNORECASE,
)
_CHINESE_IMAGE_REF_LINE = re.compile(
    r"^\s*(?:图片|图|截图|示意图|流程图|架构图|界面图|原型图|UI图|效果图|界面|原型)\s*(?:如下|所示|如下所示|参考|说明|见|参见)?\s*[:：]?\s*[^\n]*$",
    re.IGNORECASE,
)

# 章节编号前缀：「第N章」「(一)」「2.1」「3、」「1.」「1 」
_MODULE_NUMBER_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"第[一二三四五六七八九十百千\d]+[章节部分条篇]\s*"
    r"|[（(][一二三四五六七八九十百千\d]+[）)]\s*"
    r"|\d{1,2}(?:[.．]\d{1,2})+[.、．)）]?\s*"
    r"|\d{1,2}[.、．)）]\s*"
    r"|\d{1,2}\s+(?=\S)"
    r")"
)


def _sanitize_module_name(module: str) -> str:
    """清洗模块名：去掉章节编号前缀、图片引用、Markdown 格式符号、表格分隔符，合并空格。

    编号前缀属于文档结构信息，不属于模块语义（"22. 接口清单" → "接口清单"）。
    """
    if not module:
        return ""
    cleaned = _MODULE_NUMBER_PREFIX_RE.sub("", module)
    cleaned = _IMAGE_IN_BRACKETS_RE.sub("", cleaned)
    cleaned = _MD_IMAGE_RE.sub("", cleaned)
    cleaned = _MD_FORMAT_RE.sub(" ", cleaned)
    cleaned = _MD_TABLE_SEP_RE.sub(" ", cleaned)
    cleaned = _EXTRA_SPACES_RE.sub(" ", cleaned).strip()
    return cleaned[:_MODULE_MAX_LEN].strip()


def _extract_heading_from_text(text: str) -> str:
    """从切片文本中提取第一个标题作为 module 回退。"""
    if not text:
        return ""
    m = _HEADING_EXTRACT_RE.match(text)
    if m:
        title = next((g for g in m.groups() if g), None)
        if title:
            return title.strip()
    return ""


def _resolve_module_for_chunk(
    chunk: dict[str, Any],
    all_chunks: list[dict[str, Any]] = None,
    chunk_index: int = -1,
    hint_text: str = "",
) -> str:
    """统一解析切片 module：
      0. ★ child_sections 精确归属（2026-10-23 根因修复）：
            若切片被短章节合并过（child_sections 含多个原始 H2），
            且 hint_text（operation/condition/source_quote 拼接）非空，
            则按 child_sections 标题在原文中分段，找到 hint_text 匹配最多的子段，
            返回对应的 child_section 标题。
      1. section 字段 → 首 H2（向后兼容兜底）
      2. 从 text 开头抽首 H2 → 兜底
      3. 向前向后扫描相邻切片章节 → v23 根因C 继承
      4. "未分类模块" → 最终兜底

    Args:
        chunk: 当前切片
        all_chunks: 全部切片列表（可选，用于前后扫描）
        chunk_index: 当前切片在 all_chunks 中的索引（可选）
        hint_text: 精确归属线索文本（operation/condition/source_quote 拼接）
    """
    # ── 0. child_sections 精确归属 ──
    _child_sections = chunk.get("child_sections") or []
    if len(_child_sections) > 1 and hint_text and hint_text.strip():
        _text = chunk.get("text", "") or ""
        # 在 chunk.text 里找每个 child_section 标题出现的位置
        _positions = []  # [(start, section_title), ...]
        for _sec in _child_sections:
            _idx = _text.find(_sec)
            if _idx >= 0:
                _positions.append((_idx, _sec))
        if len(_positions) >= 2:
            # 按位置排序，每个子段 = 从标题到下一个标题之前
            _positions.sort(key=lambda x: x[0])
            _hint_lower = hint_text.lower()
            _best_sec = None
            _best_hits = 0
            for _k in range(len(_positions)):
                _seg_start = _positions[_k][0]
                _seg_end = (
                    _positions[_k + 1][0]
                    if _k + 1 < len(_positions)
                    else len(_text)
                )
                _seg_text = _text[_seg_start:_seg_end].lower()
                _keywords = _extract_hint_keywords(hint_text)
                _hits = sum(1 for _kw in _keywords if _kw.lower() in _seg_text)
                if _hits > _best_hits:
                    _best_hits = _hits
                    _best_sec = _positions[_k][1]
            if _best_sec and _best_hits > 0:
                _sanitized = _sanitize_module_name(_best_sec)
                if _sanitized and len(_sanitized) >= 2:
                    return _sanitized

    # ── 1. 当前切片的 section 字段 ──
    candidates = [
        chunk.get("section", ""),
        _extract_heading_from_text(chunk.get("text", "")),
    ]
    for candidate in candidates:
        sanitized = _sanitize_module_name(candidate)
        if sanitized and len(sanitized) >= 2:
            return sanitized

    # ── 3. 向前向后扫描相邻切片找最近的章节标题 ──
    if all_chunks and 0 <= chunk_index < len(all_chunks):
        # 向前扫描（最多5个切片）
        for j in range(chunk_index - 1, max(chunk_index - 6, -1), -1):
            if j < 0:
                break
            prev_chunk = all_chunks[j]
            prev_candidates = [
                prev_chunk.get("section", ""),
                _extract_heading_from_text(prev_chunk.get("text", "")),
            ]
            for cand in prev_candidates:
                sanitized = _sanitize_module_name(cand)
                if sanitized and len(sanitized) >= 2:
                    return sanitized
        # 向后扫描（最多5个切片）
        for j in range(chunk_index + 1, min(chunk_index + 6, len(all_chunks))):
            next_chunk = all_chunks[j]
            next_candidates = [
                next_chunk.get("section", ""),
                _extract_heading_from_text(next_chunk.get("text", "")),
            ]
            for cand in next_candidates:
                sanitized = _sanitize_module_name(cand)
                if sanitized and len(sanitized) >= 2:
                    return sanitized

    return "未分类模块"


def _clean_inline_url(line: str) -> str:
    """处理行内 URL：保留锚文本，仅把裸 URL 替换为占位符。"""
    if not line:
        return line

    def _replace_url(m: re.Match) -> str:
        url = m.group(0)
        # 检查 URL 前后是否有文字（简单判断：不是整行只有 URL）
        start, end = m.span()
        before = line[:start].strip()
        after = line[end:].strip()
        # 如果 URL 是独立括号/方括号里的，直接删除
        if before.endswith("(") and after.startswith(")"):
            return ""
        if before.endswith("[") and after.startswith("]"):
            return ""
        # 否则保留占位符
        return "[URL]"

    return _URL_PATTERN.sub(_replace_url, line).strip()


def preprocess_raw_text(raw_text: str) -> str:
    """切片前预处理：过滤噪音行，保留行内 URL 上下文。

    过滤规则：
      1. 纯空白行删除
      2. 纯 URL 行删除
      3. 纯图片路径行删除
      4. 行内 URL 保留上下文，替换为 [URL] 占位
    """
    if not raw_text:
        return raw_text

    lines = raw_text.split("\n")
    cleaned_lines = []

    for line in lines:
        if _EMPTY_LINE.match(line):
            continue
        if _PURE_URL_LINE.match(line):
            continue
        if _PURE_IMAGE_LINE.match(line):
            continue
        if _CHINESE_IMAGE_REF_LINE.match(line):
            continue
        cleaned_line = _clean_inline_url(line)
        if cleaned_line:
            cleaned_lines.append(cleaned_line)

    result = "\n\n".join(cleaned_lines)
    _log.info("[_fd_preprocess] 预处理: %d 字符 → %d 字符", len(raw_text), len(result))
    return result


def preprocess_and_extract_tables(raw_text: str) -> tuple[str, list[dict[str, Any]]]:
    """预处理入口：提取 Markdown 表格为独立切片，并返回清洗后的普通文本。

    返回:
        cleaned_text: 去掉表格后的文本（表格位置用占位说明替代）
        table_chunks: 每个表格一个切片，结构符合 requirement_slices 字段约定
    """
    if not raw_text or not raw_text.strip():
        return raw_text, []

    tables = extract_markdown_tables(raw_text)
    if not tables:
        cleaned = preprocess_raw_text(raw_text)
        return cleaned, []

    # 从文本中剥离表格，避免普通切片里出现 |
    cleaned = strip_tables_from_text(raw_text)
    cleaned = preprocess_raw_text(cleaned)

    table_chunks: list[dict[str, Any]] = []
    for idx, table in enumerate(tables):
        text = table_to_feature_list(table)  # 功能清单表格自动转为易读格式
        chunk_id = f"req_raw:table:{idx}"
        table_chunks.append(
            {
                "chunk_id": chunk_id,
                "text": text,
                "section": _sanitize_module_name(table.get("title", "")),
                "slice_type": "table",
                "table_meta": {
                    "headers": table.get("headers", []),
                    "rows": table.get("rows", []),
                    "title": table.get("title", ""),
                },
                "prev_context": "",
                "next_context": "",
            }
        )

    _log.info("[_fd_preprocess] 提取 %d 个表格切片", len(table_chunks))
    return cleaned, table_chunks
