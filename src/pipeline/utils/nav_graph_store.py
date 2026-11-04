# -*- coding: utf-8 -*-
"""★ P0-3 3b: 导航图结构化存储（nav_graph_nodes / nav_graph_edges 读写）。

职责：提供结构化导航节点/边存储（原 system_nav_maps 纸地图已于 2026-08-05 物理 DROP，由 nav_graph 取代）。
- Step1 调用 write_nav_graph 写入（从 nav_map flat_list / menu_groups）
- Step3 调用 read_nav_graph_nodes 读取（替 nav_json 大 JSON）

★ 通用性：基于真实 DOM 爬取结构，零业务术语，跨系统/行业/页面通用。
★ 历史降级点：旧实现在 nav_graph 为空时降级读 system_nav_maps；该表已于 2026-08-05 物理 DROP，降级分支已随 P0-1 读侧迁移一并移除。
★ 幂等：先 DELETE 后 INSERT，重跑不产生重复。
"""
from __future__ import annotations
from src.core.guarded import guard

import json as _json
import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 写入
# ═══════════════════════════════════════════════════════════════


def write_nav_graph(
    project: str,
    version_label: str,
    flat_list: list[dict[str, Any]],
    menu_groups: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """从 nav_map 结构写入 nav_graph_nodes + nav_graph_edges（幂等：先删后插）。

    参数:
        project:       项目名
        version_label: 版本标签
        flat_list:     nav_map["flat_list"]，每项含 text/url/path/page_elements/source/real_url/sidebar_text
        menu_groups:   nav_map["menu_groups"]，用于提取父子层级边（可选）

    返回:
        {"success": bool, "nodes": int, "edges": int, "error": str}
    """
    if not project or not version_label:
        return {
            "success": False,
            "nodes": 0,
            "edges": 0,
            "error": "project/version_label 为空",
        }
    if not flat_list:
        return {"success": False, "nodes": 0, "edges": 0, "error": "flat_list 为空"}

    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        # 1. 提取去重后的 nodes
        node_rows, title_to_node_id = _build_node_rows(
            flat_list, project, version_label
        )
        if not node_rows:
            _log.warning("[nav_graph_store] 无有效节点可写入")
            return {"success": False, "nodes": 0, "edges": 0, "error": "无有效节点"}

        # 3. 幂等删除：删 nodes（edges 有 FK ON DELETE CASCADE 自动级联）
        conn.execute(
            "DELETE FROM nav_graph_nodes WHERE project=%s AND version_label=%s",
            (project, version_label),
        )

        # 4. 批量插入 nodes
        conn.executemany(
            """INSERT INTO nav_graph_nodes
               (project, version_label, page_url, page_path, page_title,
                element_count, page_elements, snapshot_id, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            node_rows,
        )

        # 5. 提取 edges — INSERT nodes 后回查 id 解析
        # ★ edges.from_node/to_node 是 BIGINT(引用 node.id)，不能直接填 url
        edge_specs = _collect_edge_specs(menu_groups or [], flat_list, title_to_node_id)
        edge_rows: list[tuple] = []
        if edge_specs:
            # 回查刚插入的 node id（url→id 一步解析，与节点键同源）
            url_to_id = _load_url_to_id_map(conn, project, version_label)
            edge_rows, dropped = _resolve_edge_rows(
                edge_specs, url_to_id, project, version_label
            )
            if dropped:
                # 不静默：边被丢弃说明节点/边键不一致，属数据缺陷而非正常降级
                _log.warning(
                    "[nav_graph_store] %d/%d 条边因端点节点缺失被丢弃 "
                    "(project=%s, ver=%s)；示例: %s",
                    dropped,
                    len(edge_specs),
                    project,
                    version_label,
                    edge_specs[:3],
                )
        if edge_rows:
            conn.executemany(
                """INSERT INTO nav_graph_edges
                   (project, version_label, from_node, to_node,
                    trigger_action, via_tab, via_sidebar)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                edge_rows,
            )

        conn.commit()
        _log.info(
            "[nav_graph_store] 写入 %d nodes, %d edges (project=%s, ver=%s)",
            len(node_rows),
            len(edge_rows),
            project,
            version_label,
        )
        return {
            "success": True,
            "nodes": len(node_rows),
            "edges": len(edge_rows),
            "error": "",
        }
    except Exception as e:
        _log.error(
            "[nav_graph_store] 写入失败 project=%s ver=%s nodes=%d: %s",
            project,
            version_label,
            len(flat_list or []),
            e,
            exc_info=True,
        )
        if conn:
            try:
                conn.rollback()
            except Exception as _e:
                _log.error("[nav_graph_store] 回滚失败: %s", _e, exc_info=True)
        return {"success": False, "nodes": 0, "edges": 0, "error": str(e)[:200]}
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.warning("[nav_graph_store] 连接关闭异常: %s", _e, exc_info=True)


def _build_node_rows(
    flat_list: list[dict[str, Any]],
    project: str,
    version_label: str,
) -> tuple[list[tuple], dict[str, int]]:
    """从 flat_list 提取去重后的 node 行 + title→node_id 映射。

    返回:
        (node_rows, title_to_node_id)
        node_rows: [(project, ver, page_url, page_path, page_title, elem_count, snap_id, source), ...]
        title_to_node_id: {page_title: node_id}（edges 用，node_id 在 INSERT 后回填）
    """
    seen_urls: set[str] = set()
    node_rows: list[tuple] = []
    title_to_url: dict[str, str] = {}

    for item in flat_list:
        if not isinstance(item, dict):
            continue
        fields = _extract_node_fields(item)
        if not fields:
            continue

        page_url = fields["page_url"]
        # 去重：按 page_url（UNIQUE KEY 保证，但提前去重避免 executemany 冲突）
        if page_url in seen_urls:
            continue
        seen_urls.add(page_url)

        title = fields["page_title"]
        if title and title not in title_to_url:
            title_to_url[title] = page_url

        node_rows.append(
            (
                project,
                version_label,
                page_url[:1024],
                fields["page_path"][:512],
                title[:512],
                fields["element_count"],
                fields["page_elements"],
                fields["snapshot_id"],
                fields["source"],
            )
        )

    return node_rows, title_to_url


def _resolve_page_url(item: dict[str, Any]) -> str:
    """解析 flat_list item 的节点身份键 page_url（节点与边共用，保证可对齐）。

    优先 real_url（含虚拟锚点），降级 url，再降级 path。

    ★ RC1通用修复(2026-07-24): 所有含 # 锚点的虚拟URL都保留完整串作为去重键。
      修复前只处理 #sidebar=，导致 #tab=/#page=/#view= 等虚拟URL被折叠为 real_url
      → 不同 Tab 子页面 URL 相同 → 被去重丢弃 → nav_graph 缺 Tab 节点。
      通用性: 覆盖所有 SPA 框架(Ant Design/ElementUI/Vue Router)的 hash 路由。

    ★ P0-1(2026-08-05): 从 _extract_node_fields 抽出为独立函数。
      根因: 边构造侧原先用 page_title 做节点键，与节点侧的 page_url 键不同源，
      两套键无法对齐 → from_id 恒为 None → 边 100% 丢弃。抽出后边/点同源。
    """
    real_url = (item.get("real_url", "") or "").strip()
    url = (item.get("url", "") or "").strip()
    path = (item.get("path", "") or "").strip()

    if real_url and url and "#" in url:
        return url
    return real_url or url or path


def _parse_virtual_anchor(page_url: str) -> tuple[str, str, str]:
    """拆解虚拟URL ``base#sidebar=X`` / ``base#tab=Y``。

    返回:
        (base_url, sidebar_text, tab_text)；非虚拟URL 返回 (page_url, "", "")。

    通用性：只认 `#sidebar=` / `#tab=` 两个结构化锚点前缀，不含任何业务词。
    """
    if not page_url or "#" not in page_url:
        return page_url, "", ""
    base, _, anchor = page_url.partition("#")
    if not base:
        return page_url, "", ""
    if anchor.startswith("sidebar="):
        return base, anchor[len("sidebar=") :].strip(), ""
    if anchor.startswith("tab="):
        return base, "", anchor[len("tab=") :].strip()
    return base, "", ""


def _extract_node_fields(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    """从 flat_list item 提取 node 字段。

    通用性：
      - page_url 优先 real_url（绝对URL），降级 url，再降级 path
      - 虚拟URL（含 # 锚点：#sidebar=/#tab=/#page= 等）保留完整串作为去重键
      - source='dom' 标识真实爬取，其他降级为 'manual'
      - element_count 从 page_elements 计算（buttons+inputs+links）

    ★ P0-1 清理(2026-08-05): 移除 snapshot_id 关联逻辑。
      page_dom_snapshots 0 行且**全仓无任何写入方**，映射恒为空
      （实测 nav_graph_nodes 29 行 snapshot_id 全为 NULL），
      该表已随本次清理 DROP，故 snapshot_id 固定写 NULL。
    """
    text = (item.get("text", "") or item.get("name", "") or "").strip()
    if not text:
        return None

    page_url = _resolve_page_url(item)
    if not page_url:
        return None

    # page_path：取 path 部分（去掉 hash/query）
    page_path = (item.get("path", "") or "").strip() or _extract_path_only(page_url)

    # element_count：从 page_elements 计算
    _page_elements_raw = item.get("page_elements")
    element_count = _count_elements(_page_elements_raw)

    # ★ P0-3b: page_elements 结构化存 JSON（替 system_nav_maps.nav_json.page_elements）
    #   通用性: dict/list → json.dumps；str → 原样；空 → None
    page_elements_json = _serialize_page_elements(_page_elements_raw)

    # source：'dom' 标识真实爬取，其他降级为 'manual'
    src = (item.get("source", "") or "").strip().lower()
    source = "dom" if src == "dom" else "manual"

    return {
        "page_url": page_url,
        "page_path": page_path,
        "page_title": text,
        "element_count": element_count,
        "page_elements": page_elements_json,
        "snapshot_id": None,
        "source": source,
    }


def _serialize_page_elements(page_elements: Any) -> Optional[str]:
    """将 page_elements 序列化为 JSON 字符串（供 DB JSON 列存储）。

    通用性：dict/list → json.dumps；str → 原样保留；None/空 → None。
    """
    if not page_elements:
        return None
    if isinstance(page_elements, str):
        # 已是 JSON 字符串，校验有效性后原样返回
        try:
            _json.loads(page_elements)
            return page_elements
        except (ValueError, TypeError) as _e:
            _log.debug(
                "[nav_graph] page_elements JSON 校验失败(§5 影响§12导航地图): %s", _e
            )
            return None
    if isinstance(page_elements, (dict, list)):
        try:
            return _json.dumps(page_elements, ensure_ascii=False)
        except (TypeError, ValueError) as _e:
            _log.debug(
                "[nav_graph] page_elements 序列化失败(§5 影响§12导航地图): %s", _e
            )
            return None
    return None


def _extract_path_only(url: str) -> str:
    """从 URL 提取 path 部分（去掉 hash/query），通用兼容绝对/相对URL。"""
    if not url:
        return ""
    # 去掉 hash
    s = url.split("#", 1)[0]
    # 去掉 query
    s = s.split("?", 1)[0]
    # 绝对URL取 path
    if s.startswith("http://") or s.startswith("https://"):
        try:
            from urllib.parse import urlparse as _urlparse

            return _urlparse(s).path or ""
        except Exception:
            return ""
    return s


def _count_elements(page_elements: Any) -> int:
    """从 page_elements 计算可交互元素数（通用：buttons+inputs+links）。"""
    if not page_elements:
        return 0
    if isinstance(page_elements, str):
        try:
            page_elements = _json.loads(page_elements)
        except Exception:
            return 0
    if not isinstance(page_elements, dict):
        return 0
    total = 0
    for key in ("buttons", "inputs", "links", "selects", "forms"):
        val = page_elements.get(key)
        if isinstance(val, list):
            total += len(val)
    return total


def _collect_edge_specs(
    menu_groups: list[dict[str, Any]],
    flat_list: list[dict[str, Any]],
    title_to_url: dict[str, str],
) -> list[tuple[str, ...]]:
    """提取导航边规格，**以 page_url 为节点身份键**（与 nodes 同源）。

    ★ P0-1 根因修复(2026-08-05)：
      L1 现象: nav_graph_edges 恒为 0 行，导致下游 via_sidebar 菜单分组全空。
      L2 直接原因: 原实现返回 (菜单组名, 子页标题)，而 write_nav_graph 用
         `title_to_url` 解析 from_id —— 该映射只由 flat_list 的**页面标题**构建。
      L3 根因: 菜单组名是「容器」而非「页面」，永远不在 flat_list 标题集合里
         → `from_url=""` → `from_id=None` → `if from_id and to_id` 恒 False
         → 100% 边被丢弃，且无任何日志（静默）。
      修复: 边的主来源改为 flat_list 的虚拟URL（`real_url#sidebar=X` /
         `#tab=Y`），其 base 段就是父页面的真实 page_url —— 父子关系是**爬取
         事实**，无需靠标题猜测。菜单层级作为补充来源，且父组解析不到页面时
         显式计数告警（不再静默丢弃）。

    通用性（§4.2）：仅依赖 `#sidebar=` / `#tab=` 结构化锚点与 menu_groups 通用
    字段，无任何业务术语/页面路径硬编码，跨系统通用。

    返回:
        [(from_url, to_url, via_tab, via_sidebar[, link_text]), ...]

        前两路来源返回 4 元组（菜单/Tab 上下文已能提供 selector）；第三路
        （page_links）返回 5 元组，末位为链接可见文本。_resolve_edge_rows 同时
        接受两种长度 —— 既避免无谓改动前两路，也保持对既有调用方（QA 探针、
        verify 脚本手工构造的 4 元组）向后兼容。
    """
    specs: list[tuple[str, ...]] = []
    specs.extend(_edges_from_flat_list(flat_list))
    specs.extend(_edges_from_menu_groups(menu_groups, title_to_url))
    specs.extend(_edges_from_page_links(flat_list))
    return specs


def _edges_from_flat_list(
    flat_list: list[dict[str, Any]],
) -> list[tuple[str, str, str, str]]:
    """主来源：从虚拟URL 派生真实父子边（base 页 → sidebar/tab 子页）。"""
    specs: list[tuple[str, str, str, str]] = []
    for item in flat_list:
        if not isinstance(item, dict):
            continue
        child_url = _resolve_page_url(item)
        if not child_url:
            continue

        base_url, anchor_sidebar, anchor_tab = _parse_virtual_anchor(child_url)
        if base_url == child_url:
            continue  # 非虚拟URL：无父级锚点信息，交由 menu_groups 补充

        via_sidebar = (item.get("sidebar_text", "") or anchor_sidebar or "").strip()
        via_tab = (item.get("tab", "") or anchor_tab or "").strip()
        specs.append((base_url, child_url, via_tab, via_sidebar))
    return specs


def _edges_from_menu_groups(
    menu_groups: list[dict[str, Any]],
    title_to_url: dict[str, str],
) -> list[tuple[str, str, str, str]]:
    """补充来源：菜单层级父子边（父组本身是页面时才成边）。"""
    specs: list[tuple[str, str, str, str]] = []
    unresolved_groups: list[str] = []

    for mg in menu_groups:
        if not isinstance(mg, dict):
            continue
        parent_name = (mg.get("group_name", "") or mg.get("name", "") or "").strip()
        if not parent_name:
            continue

        parent_url = title_to_url.get(parent_name, "")
        if not parent_url:
            # 父组是纯容器（无对应页面）→ 该组子项的父子关系只能由虚拟URL提供
            unresolved_groups.append(parent_name)
            continue

        for item in mg.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            child_name = (item.get("text", "") or item.get("name", "") or "").strip()
            if not child_name or child_name == parent_name:
                continue
            child_url = title_to_url.get(child_name, "")
            if not child_url:
                continue
            via_tab = (item.get("tab", "") or "").strip()
            specs.append((parent_url, child_url, via_tab, parent_name))

    if unresolved_groups:
        # 不静默：明确区分「父组是容器（正常）」与「数据缺失（异常）」
        _log.info(
            "[nav_graph_store] %d 个菜单组无对应页面节点（纯容器，父子边改由虚拟URL派生）: %s",
            len(unresolved_groups),
            unresolved_groups[:10],
        )
    return specs


def _edges_from_page_links(
    flat_list: list[dict[str, Any]],
) -> list[tuple[str, str, str, str, str]]:
    """第三来源：从页面内真实超链接 page_elements.links 派生导航边。

    适用场景（扁平导航系统）：真实数据既无 ``#sidebar=`` / ``#tab=`` 虚拟锚点
    （_edges_from_flat_list 因 ``base_url == child_url`` 全 ``continue`` 失效），
    也无有效菜单组名（_edges_from_menu_groups 因 ``if not parent_name: continue`` 失效）。
    但页面 DOM 内的超链接 href 仍真实指向其它已知页面 —— 这是扁平 SPA 的导航事实。

    ★ F5 修复(2026-08-05)：QA 在真实库（e2e_self_check / v1.0，19 节点、0 虚拟URL、
    菜单组名为空）上实测 ``page_elements.links`` 指向已知页面的去重边有 270 条；
    旧实现三路来源在真实数据上同时失效 → nav_graph_edges 恒为 0 → 下游 via_sidebar
    菜单分组全空（L1 症状仍在）。本来源把这部分真实导航结构消费起来。

    通用性（§4.2）：仅依赖 page_elements.links[].href 与已知 page_url 的**路径匹配**
    （_extract_path_only 去 hash/query，兼容绝对/相对 URL），无任何业务术语/页面路径
    硬编码，跨系统通用。与另两路来源同源（page_url 身份键），下游 _resolve_edge_rows
    统一去重/自环过滤。

    ★ R1 修复(2026-08-05)[主理人批准方案(c)]：附带链接可见文本 ``link_text``。
    背景：QA Round2 指出本来源产出的边 ``selector`` 恒为空（_resolve_edge_rows 原取
    ``sidebar or tab``），致 ``coverage_crawl.py`` 的 ``if from_url and selector:``
    全部落空，270 条边一条都标不到 exercised —— 边数量恢复了，消费链路没打通。
    只读探针实测：真实库 270 条可匹配边 **100% 带
    text**，故用链接文本作 selector 属「还原页面上真实存在的可点击锚点」，非编造。

    注：``via_sidebar`` 仍**保持为空**。page_elements.links 仅有 href/text 两个字段，
    没有任何信息能证明该链接位于侧边栏；填入即等于替数据下无法验证的结论，会把
    「链接导航」伪装成「侧边栏导航」。空值在此诚实表达「导航上下文未知」。

    返回:
        [(from_url, to_url, via_tab, via_sidebar, link_text), ...]
        via_tab / via_sidebar 留空 —— 超链接导航无菜单/Tab 上下文，触发语义由
        (from, to) 承载，可点击性由 link_text 承载。
    """
    # 1. 收集所有已知 page_url（归一化 path → 原始 page_url）
    known: dict[str, str] = {}
    for item in flat_list:
        if not isinstance(item, dict):
            continue
        u = _resolve_page_url(item)
        if not u:
            continue
        known.setdefault(_extract_path_only(u), u)
    if not known:
        return []

    specs: list[tuple[str, str, str, str, str]] = []
    for item in flat_list:
        if not isinstance(item, dict):
            continue
        from_url = _resolve_page_url(item)
        if not from_url:
            continue

        pe = item.get("page_elements")
        if isinstance(pe, str):
            try:
                pe = _json.loads(pe)
            except (ValueError, TypeError):
                pe = None
        if not isinstance(pe, dict):
            continue
        links = pe.get("links")
        if not isinstance(links, list):
            continue

        for lk in links:
            if not isinstance(lk, dict):
                continue
            href = (lk.get("href") or lk.get("url") or "").strip()
            if not href:
                continue
            target_url = known.get(_extract_path_only(href))
            if not target_url or target_url == from_url:
                continue  # 未命中已知页面或自环 → 跳过
            # ★ R1: 链接可见文本 —— 页面上真实可点击的锚点文案，供 selector 使用
            link_text = (lk.get("text") or lk.get("title") or "").strip()
            specs.append((from_url, target_url, "", "", link_text))
    return specs


def _resolve_edge_rows(
    edge_specs: list[tuple[str, ...]],
    url_to_id: dict[str, int],
    project: str,
    version_label: str,
) -> tuple[list[tuple], int]:
    """把 (from_url, to_url, via_tab, via_sidebar[, link_text]) 解析为可插入的边行。

    接受 4 元组与 5 元组两种规格：5 元组末位为链接可见文本（page_links 来源），
    4 元组等价于 link_text 为空 —— 保持对既有调用方的向后兼容。

    过滤规则：端点缺失 / 自环 / 重复（同一 from-to-tab-sidebar 组合）。

    返回:
        (edge_rows, dropped_count)
    """
    edge_rows: list[tuple] = []
    seen: set[tuple[int, int, str, str]] = set()
    dropped = 0

    for spec in edge_specs:
        if len(spec) < 4:
            # 规格不合法：明确计数并记录，不静默丢弃（AGENTS 契约）
            dropped += 1
            _log.warning(
                "[nav_graph_store] 边规格字段不足（期望 >=4，实得 %d）已丢弃: %r",
                len(spec),
                spec,
            )
            continue
        from_url, to_url, via_tab, via_sidebar = spec[0], spec[1], spec[2], spec[3]
        link_text = spec[4] if len(spec) > 4 else ""

        from_id = url_to_id.get(from_url)
        to_id = url_to_id.get(to_url)
        if not from_id or not to_id:
            dropped += 1
            continue
        if from_id == to_id:
            continue  # 自环无导航语义，静默跳过属正常

        tab = (via_tab or "")[:128]
        sidebar = (via_sidebar or "")[:128]
        # 去重键**不含 link_text**：同一 (from,to,tab,sidebar) 即同一条导航关系，
        # 多个文案不同的锚点指向同一页面不应放大边数（首个文案胜出）。
        key = (from_id, to_id, tab, sidebar)
        if key in seen:
            continue
        seen.add(key)

        # trigger_action：selector 用真实可点击文本
        #   优先级 sidebar > tab > link_text
        # ★ R1 修复(2026-08-05)[主理人批准方案(c)]：追加 link_text 兜底。
        #   原实现为 `sidebar or tab`，链接派生边两者皆空 → selector 恒为空 →
        #   coverage_crawl.py 的 `if from_url and selector:` 永不成立，270 条边
        #   一条都标不到 exercised。link_text 是页面上真实存在的锚点文案
        #   （探针实测真实库覆盖率 100%），用作 selector 属还原事实而非编造。
        #   注意：此处**不回填 via_sidebar** —— 无证据证明链接位于侧边栏，
        #   保持空值以诚实表达「导航上下文未知」。
        selector = sidebar or tab or (link_text or "")[:128]
        trigger = _json.dumps(
            {"type": "click", "selector": selector},
            ensure_ascii=False,
        )
        edge_rows.append(
            (
                project,
                version_label,
                from_id,
                to_id,
                trigger,
                tab,
                sidebar,
            )
        )

    return edge_rows, dropped


def _load_url_to_id_map(conn, project: str, version_label: str) -> dict[str, int]:
    """回查刚插入的 nav_graph_nodes 的 page_url→id 映射（edges 解析用）。

    在 INSERT nodes 后、commit 前调用（同事务内可见未提交数据）。

    ★ P0-1: 失败必须上抛 —— 该映射为空会让所有边被丢弃，若吞掉异常，
    表现就是"边写了 0 行"却无任何错误，正是本次要根治的静默失败模式。
    """
    url_to_id: dict[str, int] = {}
    rows = (
        conn.execute(
            "SELECT id, page_url FROM nav_graph_nodes "
            "WHERE project=%s AND version_label=%s",
            (project, version_label),
        ).fetchall()
        or []
    )
    for row in rows:
        nid = int(row.get("id") or 0)
        url = (row.get("page_url", "") or "").strip()
        if nid > 0 and url:
            url_to_id[url] = nid
    return url_to_id


# ═══════════════════════════════════════════════════════════════
# 读取
# ═══════════════════════════════════════════════════════════════


def read_nav_graph_nodes(project: str, version_label: str) -> list[dict[str, Any]]:
    """读取结构化导航节点（替 system_nav_maps.nav_json flat_list）。

    返回:
        [{page_url, page_path, page_title, element_count, snapshot_id, source}, ...]
        按 source='dom' 优先排序（真实爬取在前）。
    """
    if not project or not version_label:
        return []

    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        rows = (
            conn.execute(
                """SELECT page_url, page_path, page_title, element_count,
                      page_elements, snapshot_id, source
               FROM nav_graph_nodes
               WHERE project=%s AND version_label=%s
               ORDER BY (source='dom') DESC, page_title""",
                (project, version_label),
            ).fetchall()
            or []
        )
        # ★ P0-3b: page_elements JSON → dict（便于 Step3 _rule_extractor 直接使用）
        result: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            pe = d.get("page_elements")
            if isinstance(pe, str):
                with guard("read_nav_graph_nodes: d['page_elements'] = _json.loads(pe)", log=_log):
                    d["page_elements"] = _json.loads(pe)
            result.append(d)
        return result
    except Exception as e:
        _log.warning("[nav_graph_store] 读取失败(降级空): %s", str(e)[:120])
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[nav_graph_store] 操作异常: %s", _e, exc_info=True)


def read_nav_graph_edges(project: str, version_label: str) -> list[dict[str, Any]]:
    """读取导航边（页面间转移，喂 op_cache 状态转移）。

    返回:
        [{from_node, to_node, trigger_action, via_tab, via_sidebar,
          from_title, to_title}, ...]
    """
    if not project or not version_label:
        return []

    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        rows = (
            conn.execute(
                """SELECT e.from_node, e.to_node, e.trigger_action, e.via_tab, e.via_sidebar,
                      fn.page_title AS from_title, tn.page_title AS to_title
               FROM nav_graph_edges e
               LEFT JOIN nav_graph_nodes fn ON fn.id = e.from_node
               LEFT JOIN nav_graph_nodes tn ON tn.id = e.to_node
               WHERE e.project=%s AND e.version_label=%s""",
                (project, version_label),
            ).fetchall()
            or []
        )
        return [dict(row) for row in rows]
    except Exception as e:
        _log.warning("[nav_graph_store] edges 读取失败(降级空): %s", str(e)[:120])
        return []
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[nav_graph_store] 操作异常: %s", _e, exc_info=True)


def has_nav_graph(project: str, version_label: str) -> bool:
    """检查是否存在 nav_graph 数据（旧 system_nav_maps 降级分支已于 2026-08-05 随该表 DROP 移除）。"""
    if not project or not version_label:
        return False
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM nav_graph_nodes "
            "WHERE project=%s AND version_label=%s",
            (project, version_label),
        ).fetchone()
        return int((row or {}).get("cnt", 0)) > 0
    except Exception as e:
        _log.debug("[nav_graph_store] has_nav_graph 检查失败: %s", str(e)[:80])
        return False
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[nav_graph_store] 操作异常: %s", _e, exc_info=True)
