# -*- coding: utf-8 -*-
"""Markdown 表格识别与结构化提取。

用途：
  1. Step2 预处理阶段把文档中的 Markdown 表格识别为独立切片（type="table"），
     避免表格行的 `|` 污染 module/section 字段。
  2. 表格切片进入 RAG，供 Step4 校验目标系统下载的导入模板字段是否匹配。
"""

from __future__ import annotations

import re
from typing import Any


# 匹配 Markdown 表格行：至少两列，用 | 分隔
_MD_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")


def _split_table_cells(row: str) -> list[str]:
    """按 | 拆分单元格并清理两端空格。"""
    # 去掉首尾的 |
    inner = row.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in inner.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    """判断是否为 Markdown 表格分隔行（如 :--- | :---）。"""
    if not cells:
        return False
    return all(re.fullmatch(r"[:\- ]+", cell) for cell in cells if cell)


def extract_markdown_tables(raw_text: str) -> list[dict[str, Any]]:
    """从 Markdown 文本中提取所有表格。

    返回:
        [
            {
                "raw_markdown": "...",
                "title": "表格前最近的标题（可选）",
                "headers": ["列1", "列2"],
                "rows": [["a", "b"], ["c", "d"]],
                "start_line": 0,
                "end_line": 5,
            },
            ...
        ]
    """
    if not raw_text:
        return []

    lines = raw_text.split("\n")
    tables: list[dict[str, Any]] = []
    i = 0
    n = len(lines)

    while i < n:
        m = _MD_TABLE_ROW_RE.match(lines[i])
        if not m:
            i += 1
            continue

        # 找到连续表格块
        table_lines: list[str] = []
        start_line = i
        while i < n and _MD_TABLE_ROW_RE.match(lines[i]):
            table_lines.append(lines[i])
            i += 1
        end_line = i

        if len(table_lines) < 2:
            # 只有一行，构不成表格
            continue

        parsed_rows = [_split_table_cells(line) for line in table_lines]

        # 判断第二行是否为分隔行
        headers: list[str] = []
        rows: list[list[str]] = []
        if _is_separator_row(parsed_rows[1]):
            headers = parsed_rows[0]
            rows = parsed_rows[2:]
        else:
            # 无分隔行，把第一行当表头（常见简化写法）
            headers = parsed_rows[0]
            rows = parsed_rows[1:]

        # 过滤空行
        rows = [r for r in rows if any(cell.strip() for cell in r)]
        if not rows:
            continue

        # 向前找表格标题（最近的非空行，且不是表格行）
        title = ""
        for j in range(start_line - 1, -1, -1):
            prev = lines[j].strip()
            if prev and not _MD_TABLE_ROW_RE.match(prev):
                # 去掉 Markdown 标记后的纯文本作为标题
                title = re.sub(r"[#*_`]+", "", prev).strip()
                break

        tables.append(
            {
                "raw_markdown": "\n".join(table_lines),
                "title": title,
                "headers": headers,
                "rows": rows,
                "start_line": start_line,
                "end_line": end_line,
            }
        )

    return tables


def strip_tables_from_text(raw_text: str) -> str:
    """把 Markdown 表格块替换为占位说明，返回清洗后的文本。

    表格内容被提取后不再参与普通切片，避免 `|` 污染 module 名。
    """
    if not raw_text:
        return raw_text

    lines = raw_text.split("\n")
    cleaned: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        if _MD_TABLE_ROW_RE.match(lines[i]):
            # 跳过连续表格行
            start = i
            while i < n and _MD_TABLE_ROW_RE.match(lines[i]):
                i += 1
            cleaned.append(f"[表格块：第 {start + 1}-{i} 行，详见 table chunk]")
            continue
        cleaned.append(lines[i])
        i += 1

    return "\n".join(cleaned)


def table_to_text(table: dict[str, Any]) -> str:
    """把结构化表格转回易读文本，用于 RAG chunk 内容。"""
    parts: list[str] = []
    if table.get("title"):
        parts.append(f"表格：{table['title']}")
    headers = table.get("headers", [])
    if headers:
        parts.append("表头：" + " | ".join(headers))
    for idx, row in enumerate(table.get("rows", []), 1):
        parts.append(f"第{idx}行：" + " | ".join(row))
    return "\n".join(parts)


def table_to_feature_list(table: dict[str, Any]) -> str:
    """把表格转为功能清单格式，便于 AI 提取需求。

    识别模式：如果表格包含「模块」「功能」「操作」等列，则转为功能清单；
    否则回退到 table_to_text 格式。
    """
    headers = table.get("headers", [])
    h_lower = [h.lower() for h in headers]

    # 判断是否为功能清单表格
    feature_keywords = [
        "模块",
        "功能",
        "操作",
        "功能名称",
        "模块名称",
        "一级功能",
        "二级功能",
        "feature",
        "module",
        "function",
    ]
    is_feature_table = any(any(kw in h for kw in feature_keywords) for h in h_lower)

    if not is_feature_table:
        return table_to_text(table)

    # 找到关键列索引
    module_idx = None
    feature_idx = None
    version_idx = None
    for i, h in enumerate(h_lower):
        if any(kw in h for kw in ["模块", "模块名称", "module"]):
            module_idx = i
        elif any(
            kw in h
            for kw in [
                "一级功能",
                "二级功能",
                "功能",
                "功能名称",
                "feature",
                "function",
            ]
        ):
            if feature_idx is None:
                feature_idx = i
        elif any(
            kw in h for kw in ["版本", "说明", "备注", "version", "note", "remark"]
        ):
            version_idx = i

    rows = table.get("rows", [])
    if not rows:
        return table_to_text(table)

    lines: list[str] = []
    title = table.get("title", "")
    if title:
        lines.append(f"# {title}")
    lines.append("以下是从需求文档中提取的功能需求列表：")
    lines.append("")

    current_module = ""
    for row in rows:
        # 跳过纯空行
        if not any(cell.strip() for cell in row):
            continue

        module = (
            row[module_idx].strip()
            if module_idx is not None and module_idx < len(row)
            else ""
        )
        feature = (
            row[feature_idx].strip()
            if feature_idx is not None and feature_idx < len(row)
            else ""
        )
        version = (
            row[version_idx].strip()
            if version_idx is not None and version_idx < len(row)
            else ""
        )

        if module:
            current_module = module

        if feature:
            ctx = current_module if current_module else ""
            version_str = f"（{version}）" if version else ""
            entry = f"- 【{feature}】{version_str}"
            if ctx:
                entry += f" 所属模块：{ctx}"
            lines.append(entry)

    if len(lines) <= 2:
        # 没提取到功能条目，回退
        return table_to_text(table)

    return "\n".join(lines)
