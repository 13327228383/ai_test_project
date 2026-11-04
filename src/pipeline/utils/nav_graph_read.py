# -*- coding: utf-8 -*-
"""导航数据只读适配层（P0-1 读侧迁移）。

背景（根因 §10.9）：
  L1: Step3 生成的用例缺真实按钮/输入框元素，Step4 导航查不到路径。
  L2: 调用方读 `system_nav_maps.nav_json` / `page_dom_snapshots` 均返回空。
  L3: `system_nav_maps` 表**根本没有 `nav_json` / `version_label` 列**
      （实际列为 `nav_map_json`），所有 `SELECT nav_json ...` 直接抛
      `Unknown column`；且两张表均已停写、0 行。
  L4: 这些读取点全部包在 `except: _log.warning/_log.debug` 里静默吞掉，
      于是"读不到数据"表现为"库里没数据"，掩盖了真实断链。

  写侧早已迁到 `nav_graph_nodes` / `nav_graph_edges`
  （`nav_graph_store.write_nav_graph`，由 `_nav_map_builder.py:465` 调用），
  只有读侧没迁。本模块提供**形状兼容**的读接口，让调用方以最小改动切换：

    旧: SELECT nav_json FROM system_nav_maps  → nav_json["flat_list"]
    新: read_flat_list(project)

    旧: SELECT page_path, dom_summary FROM page_dom_snapshots
    新: read_dom_pages(project)   # dom_summary 由 page_elements 合成

通用性（§4.2）：
  纯结构化字段映射，不含任何业务术语/页面路径/行业规则，跨系统通用。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)

# page_elements 中会被合成进 dom_summary 的元素族（顺序即输出顺序）
# ★ R1 收尾(2026-08-05) [已复核-DeepSeek-V4-Flash 2026-08-05]：补入 "links"。
#   原三族漏掉 links，而 nav_graph_edges 的 270 条链接派生边引用的正是 links 文案，
#   导致下游（coverage_crawl / _verify_sidebar_in_dom）拿到的 dom_summary 里
#   根本不存在这些可点击文本 → 边永远匹配不上页面元素。
#   links 是真实 <a> 标签（page_elements.links 由 Step1 实爬写入），列为可点击元素属还原事实。
_ELEMENT_GROUPS: tuple[str, ...] = ("inputs", "buttons", "links", "tabs")


def resolve_version_label(project: str, version_label: Optional[str] = None) -> str:
    """确定要读取的 version_label；未指定时取该 project 最新的一版。

    旧表读取点大多只有 `project`（如 `WHERE project=%s ORDER BY id DESC LIMIT 1`），
    而 nav_graph 按 (project, version_label) 隔离，故需要一个显式解析入口。

    ★ 2026-09-23 根治（version=None → 全量返回空节点集 / 每次调用刷 WARNING）：
      根因：原实现**只**从 nav_graph_nodes 反查版本（取最新一行）。对"有版本但尚未
      产出导航图"的项目（未跑浏览器爬取、或纯 API 靶子）必然取不到 → 返回 "" →
      read_nodes 判定 version=None → WARNING 刷屏且导航上下文整体缺失。
      修复：nav_graph_nodes 无行时回退到项目 active version_label（版本的真源），
      使"版本可判定"与"导航图有无数据"解耦；后者为空是合法状态，由调用方按空集处理。
    通用性：任何项目，不依赖是否跑过导航爬取。
    """
    if version_label:
        return version_label
    if not project:
        return ""

    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT version_label FROM nav_graph_nodes WHERE project=%s "
            "ORDER BY id DESC LIMIT 1",
            (project,),
        ).fetchone()
        _ver = str((row or {}).get("version_label", "") or "")
        if _ver:
            return _ver
    except Exception as exc:
        _log.error(
            "[nav_graph_read] 解析 version_label 失败 project=%s: %s",
            project,
            exc,
            exc_info=True,
        )
        return ""
    finally:
        _close(conn)

    # ★ 回退：nav_graph_nodes 无数据 → 用项目 active version_label（版本真源）
    try:
        from src.core.versioning import current_version_label

        return str(current_version_label(project) or "")
    except Exception as exc:
        _log.debug(
            "[nav_graph_read] 回退 active version_label 失败 project=%s: %s", project, exc
        )
        return ""


def read_nodes(
    project: str, version_label: Optional[str] = None
) -> list[dict[str, Any]]:
    """读取导航节点原始行（page_url/page_path/page_title/page_elements/...）。"""
    ver = resolve_version_label(project, version_label)
    if not project or not ver:
        _log.warning(
            "[nav_graph_read] 无法确定导航版本 project=%r version=%r，返回空节点集",
            project,
            version_label,
        )
        return []
    from src.pipeline.utils.nav_graph_store import read_nav_graph_nodes

    return read_nav_graph_nodes(project, ver)


def read_edges(
    project: str, version_label: Optional[str] = None
) -> list[dict[str, Any]]:
    """读取导航边（含 from_title/to_title/via_sidebar/via_tab）。"""
    ver = resolve_version_label(project, version_label)
    if not project or not ver:
        return []
    from src.pipeline.utils.nav_graph_store import read_nav_graph_edges

    return read_nav_graph_edges(project, ver)


def read_flat_list(
    project: str, version_label: Optional[str] = None
) -> list[dict[str, Any]]:
    """以旧 `nav_json["flat_list"]` 的形状返回导航项。

    形状: [{text, name, url, real_url, path, page_elements, source, element_count}, ...]
    调用方原先怎么用 flat_list，现在可以原样使用。
    """
    items: list[dict[str, Any]] = []
    for node in read_nodes(project, version_label):
        title = str(node.get("page_title", "") or "").strip()
        url = str(node.get("page_url", "") or "").strip()
        if not title and not url:
            continue
        page_elements = node.get("page_elements")
        items.append(
            {
                "text": title,
                "name": title,
                "url": url,
                "real_url": url,
                "path": str(node.get("page_path", "") or ""),
                "page_elements": (
                    page_elements if isinstance(page_elements, dict) else {}
                ),
                "element_count": int(node.get("element_count") or 0),
                "source": str(node.get("source", "") or ""),
            }
        )
    return items


def read_dom_pages(
    project: str, version_label: Optional[str] = None
) -> list[dict[str, Any]]:
    """以旧 `page_dom_snapshots` 的行形状返回页面（dom_summary 由结构化元素合成）。

    形状: [{page_url, page_path, page_title, dom_summary, element_count}, ...]
    """
    pages: list[dict[str, Any]] = []
    for node in read_nodes(project, version_label):
        page_elements = node.get("page_elements")
        pages.append(
            {
                "page_url": str(node.get("page_url", "") or ""),
                "page_path": str(node.get("page_path", "") or ""),
                "page_title": str(node.get("page_title", "") or ""),
                "dom_summary": build_dom_summary(page_elements),
                "element_count": int(node.get("element_count") or 0),
            }
        )
    return pages


def build_dom_summary(page_elements: Any) -> str:
    """把结构化 page_elements 合成为 Step3 可解析的 dom_summary 文本。

    输出格式与 Step1 原产物一致（`_control_dom_utils._parse_page_elements_line`
    的输入契约）：

        [PAGE_ELEMENTS] INPUTS: a, b | BUTTONS: c, d | LINKS: e | TABS: f

    通用性：只依赖 inputs/buttons/links/tabs 四族通用控件，不含业务语义。
    """
    if isinstance(page_elements, str):
        import json as _json

        try:
            page_elements = _json.loads(page_elements)
        except (ValueError, TypeError) as exc:
            _log.warning("[nav_graph_read] page_elements 非法 JSON，跳过合成: %s", exc)
            return ""
    if not isinstance(page_elements, dict) or not page_elements:
        return ""

    segments: list[str] = []
    for group in _ELEMENT_GROUPS:
        rendered = _render_group(group, page_elements.get(group))
        if rendered:
            segments.append(f"{group.upper()}: {rendered}")
    if not segments:
        return ""
    return "[PAGE_ELEMENTS] " + " | ".join(segments)


def _render_group(group: str, values: Any) -> str:
    """把单个元素族渲染为逗号分隔文本。"""
    if not isinstance(values, list) or not values:
        return ""
    rendered: list[str] = []
    for item in values:
        text = _render_element(group, item)
        if text:
            rendered.append(text)
    return ", ".join(rendered)


def _render_element(group: str, item: Any) -> str:
    """渲染单个控件为 dom_summary item 文本。

    inputs 用 ``文本(placeholder)`` 形态，其余用纯文本。
    """
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""

    text = str(item.get("text", "") or item.get("label", "") or "").strip()
    if group == "inputs":
        placeholder = str(item.get("placeholder", "") or "").strip()
        if placeholder:
            return f"{text}({placeholder})" if text else f"({placeholder})"
        return text
    return text


def read_menu_groups(
    project: str, version_label: Optional[str] = None
) -> list[dict[str, Any]]:
    """以旧 `nav_json["menu_groups"]` 的形状返回菜单分组。

    形状: [{group_name, items: [{text, url, parent_path, ...}], tab_items: [...]}]

    合成规则（通用，无业务硬编码）：
      - 有 edges：按 `via_sidebar` 聚合成组，组内 item 为该边的目标节点，
        `parent_path` 填 via_sidebar（还原旧结构的父级语义）。
      - 无 edges：退化为单个隐式分组，包含全部节点（`parent_path` 为空）。
        这不是"降级失败"，而是"该系统导航本就是扁平的"的忠实表达。
      - `tab_items`：URL 含 `#tab=` 的节点（SPA Tab 子页面），供上游 tab bonus 匹配。
    """
    nodes = read_nodes(project, version_label)
    if not nodes:
        return []

    by_title: dict[str, dict[str, Any]] = {}
    tab_items: list[dict[str, Any]] = []
    for node in nodes:
        title = str(node.get("page_title", "") or "").strip()
        url = str(node.get("page_url", "") or "").strip()
        if not title:
            continue
        page_elements = node.get("page_elements")
        item = {
            "text": title,
            "name": title,
            "url": url,
            "real_url": url,
            "path": str(node.get("page_path", "") or ""),
            "parent_path": "",
            # 调用方（如 _fallback_cases）会从选中项直接取 page_elements.buttons
            # 推断提交按钮，故 item 必须与 flat_list 携带同样的元素信息。
            "page_elements": page_elements if isinstance(page_elements, dict) else {},
            "element_count": int(node.get("element_count") or 0),
        }
        by_title.setdefault(title, item)
        if "#tab=" in url:
            tab_items.append(dict(item))

    grouped: dict[str, list[dict[str, Any]]] = {}
    for edge in read_edges(project, version_label):
        group_name = str(edge.get("via_sidebar", "") or "").strip()
        child = str(edge.get("to_title", "") or "").strip()
        if not group_name or not child or child not in by_title:
            continue
        item = dict(by_title[child])
        item["parent_path"] = group_name
        grouped.setdefault(group_name, []).append(item)

    if grouped:
        used = {i["text"] for items in grouped.values() for i in items}
        groups = [
            {"group_name": name, "name": name, "items": items, "tab_items": tab_items}
            for name, items in grouped.items()
        ]
        leftover = [i for t, i in by_title.items() if t not in used]
        if leftover:
            groups.append(
                {
                    "group_name": "",
                    "name": "",
                    "items": leftover,
                    "tab_items": tab_items,
                }
            )
        return groups

    # 无边数据：扁平导航，单隐式分组
    return [
        {
            "group_name": "",
            "name": "",
            "items": list(by_title.values()),
            "tab_items": tab_items,
        }
    ]


def read_nav_map(project: str, version_label: Optional[str] = None) -> dict[str, Any]:
    """以旧 `nav_json` 的顶层结构返回整张导航地图。

    形状: {"flat_list": [...], "menu_groups": [...]}

    供原先写 `nav = json.loads(row["nav_json"])` 的调用点一行替换，
    后续 `nav.get("flat_list") / nav.get("menu_groups")` 用法完全不变。
    """
    return {
        "flat_list": read_flat_list(project, version_label),
        "menu_groups": read_menu_groups(project, version_label),
    }


def has_nav_data(project: str, version_label: Optional[str] = None) -> bool:
    """是否存在可用导航数据（调用方据此决定跳过 or 阻断，而非静默降级）。"""
    return bool(read_nodes(project, version_label))


def _close(conn) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception as exc:
        _log.debug("[nav_graph_read] 连接关闭异常: %s", exc, exc_info=True)
