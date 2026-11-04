# -*- coding: utf-8 -*-
"""
parser._table_parser — 表格解析 + 版本过滤
===========================================
parse_table, filter_by_version, list_versions
"""


from ._models import COLUMN_ALIASES


def parse_table(body: str) -> list[dict[str, str]]:
    """
    解析 Markdown 表格。
    不依赖列顺序 —— 按表头文字映射到标准字段。

    Returns:
        [{module, feature, version, prd_priority, notes}, ...]
    """
    lines = body.split("\n")
    table_lines = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            table_lines.append(stripped)
            in_table = True
        elif in_table:
            break  # 非空非表格行 = 表格结束

    if len(table_lines) < 3:
        return []

    # 解析表头
    header_cells = [c.strip() for c in table_lines[0].split("|")[1:-1]]
    if not header_cells:
        return []

    # 映射列索引 → 标准字段
    col_map: dict[int, str] = {}
    for idx, cell in enumerate(header_cells):
        cell_clean = cell.strip().lower().replace(" ", "")
        for canonical, aliases in COLUMN_ALIASES.items():
            for alias in aliases:
                alias_clean = alias.lower().replace(" ", "")
                if alias_clean in cell_clean or cell_clean in alias_clean:
                    col_map[idx] = canonical
                    break
            if idx in col_map:
                break

    # 至少要有 module 或 feature 列
    if "module" not in col_map.values() and "feature" not in col_map.values():
        return []

    # 解析数据行（跳过第二行分隔符 `|---|---|`）
    results = []
    for row_line in table_lines[2:]:
        cells = [c.strip() for c in row_line.split("|")[1:-1]]
        if not cells or all(c in ("", "-", "—") for c in cells):
            continue

        row: dict[str, str] = {}
        for idx, cell in enumerate(cells):
            if idx in col_map:
                row[col_map[idx]] = cell

        # 至少要有模块或功能点
        if row.get("module") or row.get("feature"):
            row.setdefault("version", "")
            row.setdefault("prd_priority", "")
            row.setdefault("notes", "")
            results.append(row)

    return results


def filter_by_version(rows: list[dict], version: str) -> list[dict]:
    """按版本号过滤表格行。空版本 = 不过滤。"""
    if not version:
        return rows
    version = version.strip()
    return [
        r
        for r in rows
        if r.get("version", "").strip() == version
        or r.get("version", "").strip().startswith(version)
    ]


def list_versions(rows: list[dict]) -> str:
    """列出表格中所有版本号。"""
    versions = sorted(set(x for r in rows if (x := r.get("version", "").strip())))
    return ", ".join(versions) if versions else "(无)"
