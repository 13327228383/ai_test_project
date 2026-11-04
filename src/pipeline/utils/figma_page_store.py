# -*- coding: utf-8 -*-
"""
Figma 事实表存储模块
====================
从 Figma document 中提取页面/节点/组件，写入 3 张独立事实表。
RAG 索引重建时从这 3 张表读取。

使用方式：
    from src.pipeline.utils.figma_page_store import save_figma_to_tables
    stats = save_figma_to_tables(document, components, pages, project, version_label)
"""

import hashlib
from src.core.guarded import guard
import json
import logging

_log = logging.getLogger(__name__)


def save_figma_to_tables(
    document: dict,
    components: dict,
    pages: list[dict],
    project: str,
    version_label: str = "",
) -> dict[str, int]:
    """从 Figma document 提取并持久化 pages / nodes / components 到独立表。

    返回: {"pages": N, "nodes": N, "components": N}
    """
    stats = {"pages": 0, "nodes": 0, "components": 0}

    if not document or not pages:
        _log.warning("[figma_page_store] document 或 pages 为空，跳过保存")
        return stats

    with guard("save_figma_to_tables: stats['pages'] = _save_pages(documen", log=_log, level=logging.WARNING):
        stats["pages"] = _save_pages(document, pages, project, version_label)

    with guard("save_figma_to_tables: stats['nodes'] = _save_nodes(documen", log=_log, level=logging.WARNING):
        stats["nodes"] = _save_nodes(document, pages, project, version_label)

    with guard("save_figma_to_tables: stats['components'] = _save_componen", log=_log, level=logging.WARNING):
        stats["components"] = _save_components(components, project, version_label)

    _log.info(
        "[figma_page_store] 保存完成: %d pages, %d nodes, %d components",
        stats["pages"],
        stats["nodes"],
        stats["components"],
    )

    # ★ P2-11 接入(2026-07-25): figma 持久化后索引到 multimodal_chunks（RAG 防幻觉）
    #   根因: index_figma 原在 src 下零调用者 → multimodal_chunks 缺 figma 类型
    #         → Step3 联合检索退化为单模态（仅 dom）→ 防幻觉能力降级
    #   通用性: content=page name，不含业务术语；与 dom_store.py 的 index_dom_snapshots 模式一致
    #   降级: 索引失败非阻断，返回 0，Step3 降级无 figma RAG 上下文
    #   职责边界: figma_page_store 只写索引，检索由 Step3 _contexts 负责
    with guard("save_figma_to_tables: from src.rag.integration import inde", log=_log, level=logging.WARNING):
        from src.rag.integration import index_figma

        _rag_count = index_figma(project, version_label, pages)
        if _rag_count > 0:
            _log.info("[figma_page_store] ✓ RAG figma 索引: %d chunks", _rag_count)

    return stats


# ═══════════════════════════════════════════════════════════════
# Pages
# ═══════════════════════════════════════════════════════════════


def _unwrap_figma_response(node_data: dict) -> dict:
    """★ v25 根因1 修复(2026-07-21): 解包 Figma API 响应的 document 包裹。

    根因: Figma /v1/files/:key/nodes 端点返回的 response_json 结构是
        {"document": {...真正的节点树...}, "components": {...}, ...}
    而 _collect_and_save_nodes / parse_figma_document 期望的是节点树本身。
    原代码直接传 node_data 给 _collect_and_save_nodes，导致：
      - 节点 type=??? (因为 node_data 没有 "type" 字段，只有 "document" 字段)
      - 递归时 children=[] (因为 node_data 没有 "children" 字段)
      - 最终 figma_nodes 表 0 行入库

    通用性: 兼容三种格式
      1. 标准 Figma API 响应: {"document": {...}} → 取 document
      2. 已解包节点: {"id": ..., "type": ..., "children": [...]} → 直接返回
      3. 其他格式: 返回原值（让下游处理）

    覆盖性: 影响 _save_pages 和 _save_nodes_from_mcp_cache 两条路径。
    合规: AGENTS.md §10.8 禁止治标不治本 — 修真正根因（document 包裹）。
    """
    if not isinstance(node_data, dict):
        return node_data
    doc = node_data.get("document")
    if isinstance(doc, dict) and doc.get("id") and doc.get("type"):
        return doc
    return node_data


def _get_full_node_from_mcp_cache(
    conn, page_id: str, project: str, version_label: str
) -> dict:
    """★ v25 根因1 修复(2026-07-21): 从 figma_mcp_cache 取页面完整节点树。

    当 depth=2 全量文档的页面节点 children 为空（Figma 截断）时，
    从 figma_mcp_cache 表的 depth=6 per-node 缓存补充完整节点树。

    通用性: 任何 Figma 设计稿都适用（不依赖业务/系统/页面类型）。
    覆盖性: 影响 _save_pages（补 ui_structure）+ _save_nodes（补 figma_nodes 表）。
    """
    try:
        row = conn.execute(
            "SELECT response_json FROM figma_mcp_cache "
            "WHERE project=%s AND version_label=%s AND depth=6 AND node_id=%s "
            "LIMIT 1",
            (project, version_label, page_id),
        ).fetchone()
        if not row:
            return {}
        raw = row["response_json"] if isinstance(row, dict) else row[0]
        node_data = json.loads(raw) if isinstance(raw, str) else raw
        return _unwrap_figma_response(node_data) if isinstance(node_data, dict) else {}
    except Exception as _e:
        _log.debug(
            "[figma_page_store] _get_full_node_from_mcp_cache 异常: %s",
            _e,
            exc_info=True,
        )
        return {}


def _save_pages(
    document: dict, pages: list[dict], project: str, version_label: str
) -> int:
    """保存顶层页面到 figma_pages 表。"""
    from src.core.db_manager import get_connection

    # 构建 page_id → full_node 的快速查找
    node_by_id = _build_node_index(document)

    conn = get_connection()
    count = 0
    try:
        for p in pages:
            pid = p.get("id", "")
            if not pid:
                continue
            name = str(p.get("name", ""))[:500]
            canvas = str(p.get("canvas", ""))[:500]
            ptype = str(p.get("type", "FRAME"))[:50]

            # 生成 UI 结构描述
            # ★ v25 根因1-B 修复(2026-07-21): depth=2 截断时从 mcp_cache depth=6 补全
            #   原bug: document 是 depth=2 截断树，page_node.children=[]，
            #          parse_figma_document 返回"（未从 Figma 设计稿中提取到 UI 结构信息）"
            #          导致 figma_pages.ui_structure 全为空字符串。
            #   修复: 优先用 document 树的 page_node；children 为空时从 mcp_cache 补全。
            full_node = node_by_id.get(pid) or {}
            if not full_node.get("children"):
                # depth=2 截断 → 从 mcp_cache depth=6 拿完整节点树
                cached = _get_full_node_from_mcp_cache(
                    conn, pid, project, version_label
                )
                if cached:
                    full_node = cached

            ui_structure = ""
            element_count = 0
            if full_node:
                try:
                    from src.pipeline.steps.step1.figma_parser import (
                        parse_figma_document,
                    )

                    ui_structure = parse_figma_document(full_node) or ""
                except Exception as _e:
                    _log.debug(
                        "[figma_page_store] _save_pages 异常: %s", _e, exc_info=True
                    )
                element_count = _count_elements(full_node)

            conn.execute(
                "INSERT INTO figma_pages (project, version_label, page_id, name, canvas, "
                "page_type, ui_structure, element_count) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), canvas=VALUES(canvas), "
                "page_type=VALUES(page_type), ui_structure=VALUES(ui_structure), "
                "element_count=VALUES(element_count), "
                "updated_at=NOW()",
                (
                    project,
                    version_label,
                    pid,
                    name,
                    canvas,
                    ptype,
                    ui_structure,
                    element_count,
                ),
            )
            count += 1
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception as _e:
            _log.debug("[figma_page_store] 非关键异常: %s", _e, exc_info=True)
    return count


# ═══════════════════════════════════════════════════════════════
# Nodes
# ═══════════════════════════════════════════════════════════════


def _save_nodes(
    document: dict, pages: list[dict], project: str, version_label: str
) -> int:
    """递归遍历每个页面的子节点，保存到 figma_nodes 表。

    document 可能是 depth=2 的截断树（children 为空），此时从 figma_mcp_cache
    表读取 per-node 缓存条目（depth=6）补充子节点数据。
    """
    from src.core.db_manager import get_connection

    page_ids = {p["id"] for p in pages if p.get("id")}
    node_by_id = _build_node_index(document)

    conn = get_connection()
    count = 0
    try:
        for page_id in page_ids:
            page_node = node_by_id.get(page_id)
            if not page_node:
                continue
            count += _collect_and_save_nodes(
                conn, page_node, page_id, "", project, version_label, page_ids
            )

        # ★ 如果 depth=2 截断导致 count==0，从 figma_mcp_cache 读取 per-node 缓存补充
        if count == 0 and page_ids:
            count = _save_nodes_from_mcp_cache(conn, page_ids, project, version_label)

        conn.commit()
    finally:
        try:
            conn.close()
        except Exception as _e:
            _log.debug("[figma_page_store] _save_nodes 异常: %s", _e, exc_info=True)
    return count


def _save_nodes_from_mcp_cache(
    conn, page_ids: set, project: str, version_label: str
) -> int:
    """从 figma_mcp_cache 表读取 per-node 缓存条目（depth=6），递归写入 figma_nodes 表。

    per-node 缓存的 response_json 是 Figma API 完整响应（含 document 包裹）。
    ★ v25 根因1-A 修复(2026-07-21): 解包 document 字段后再递归保存。
      原bug: 直接传 node_data 给 _collect_and_save_nodes，但 node_data 是
             {"document": {...}, "components": {...}, ...} 包裹格式，
             _collect_and_save_nodes 期望的是节点树本身。
             导致 figma_nodes 表 0 行入库（即使 mcp_cache 有 35 行有效数据）。
    """
    count = 0
    try:
        placeholders = ",".join(["%s"] * len(page_ids))
        rows = conn.execute(
            "SELECT node_id, response_json FROM figma_mcp_cache "
            "WHERE project = %%s AND version_label = %%s AND depth = 6 "
            "AND node_id IN (%s)" % placeholders,
            (project, version_label, *page_ids),
        ).fetchall()
        if not rows:
            _log.debug("[figma_page_store] figma_mcp_cache 无 per-node 缓存条目")
            return 0
        for row in rows:
            nid = row["node_id"] if isinstance(row, dict) else row[0]
            raw = row["response_json"] if isinstance(row, dict) else row[1]
            try:
                node_data = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError) as _e:
                _log.debug("[figma_page_store] 节点数据解析失败跳过(§5): %s", _e)
                continue
            if not isinstance(node_data, dict):
                continue
            # ★ v25 根因1-A: 解包 document 字段（兼容已解包格式）
            actual_node = _unwrap_figma_response(node_data)
            count += _collect_and_save_nodes(
                conn, actual_node, nid, "", project, version_label, page_ids
            )
        _log.info("[figma_page_store] 从 per-node 缓存补充了 %d 个节点", count)
    except Exception as _e:
        _log.warning(
            "[figma_page_store] _save_nodes_from_mcp_cache 异常: %s", _e, exc_info=True
        )
    return count


def _collect_and_save_nodes(
    conn,
    node: dict,
    page_id: str,
    parent_id: str,
    project: str,
    version_label: str,
    page_ids: set,
    depth: int = 0,
) -> int:
    """递归收集并保存节点。跳过顶层页面本身（它们已存为 page）。"""
    if depth > 5 or not isinstance(node, dict):
        return 0

    count = 0
    node_id = node.get("id", "")
    node_type = node.get("type", "")

    # 顶层页面节点不重复存
    if depth > 0 and node_id and node_id not in page_ids:
        name = str(node.get("name", ""))[:500]
        text_content = ""
        if node_type == "TEXT":
            text_content = str(node.get("characters", ""))[:2000]
        elif node_type in ("INSTANCE", "COMPONENT"):
            # 从 componentProperties 提取文本
            props = node.get("componentProperties", {})
            if isinstance(props, dict):
                prop_texts = []
                for v in props.values():
                    if isinstance(v, dict):
                        pv = v.get("value", "")
                        if isinstance(pv, str) and pv.strip():
                            prop_texts.append(pv.strip())
                    elif isinstance(v, str) and v.strip():
                        prop_texts.append(v.strip())
                if prop_texts:
                    text_content = " ".join(prop_texts)[:2000]

        meta = {}
        if node_type in ("INSTANCE", "COMPONENT"):
            props = node.get("componentProperties", {})
            if props:
                meta["componentProperties"] = props
        comp_id = node.get("componentId", "")
        if comp_id:
            meta["componentId"] = comp_id

        try:
            conn.execute(
                "INSERT INTO figma_nodes (project, version_label, node_id, page_id, "
                "parent_node_id, name, node_type, text_content, meta_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), node_type=VALUES(node_type), "
                "text_content=VALUES(text_content), meta_json=VALUES(meta_json)",
                (
                    project,
                    version_label,
                    node_id,
                    page_id,
                    parent_id,
                    name,
                    node_type,
                    text_content or None,
                    json.dumps(meta, ensure_ascii=False) if meta else None,
                ),
            )
            count += 1
        except Exception as _e:
            _log.debug("[figma_page_store] 非关键异常: %s", _e, exc_info=True)

    # 递归子节点
    for child in node.get("children", []):
        count += _collect_and_save_nodes(
            conn, child, page_id, node_id, project, version_label, page_ids, depth + 1
        )

    return count


# ═══════════════════════════════════════════════════════════════
# Components
# ═══════════════════════════════════════════════════════════════


def _save_components(components: dict, project: str, version_label: str) -> int:
    """保存组件库到 figma_components 表。"""
    if not components:
        return 0

    from src.core.db_manager import get_connection

    conn = get_connection()
    count = 0
    try:
        for comp_id, comp_info in components.items():
            if not isinstance(comp_info, dict):
                continue
            name = str(comp_info.get("name", comp_id))[:500]
            description = _format_component_description(comp_info)
            variant_options = _extract_variant_options(comp_info)

            conn.execute(
                "INSERT INTO figma_components (project, version_label, component_id, "
                "name, description, variant_options) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), description=VALUES(description), "
                "variant_options=VALUES(variant_options)",
                (
                    project,
                    version_label,
                    comp_id,
                    name,
                    description,
                    (
                        json.dumps(variant_options, ensure_ascii=False)
                        if variant_options
                        else None
                    ),
                ),
            )
            count += 1
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception as _e:
            _log.debug("[figma_page_store] 非关键异常: %s", _e, exc_info=True)
    return count


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _build_node_index(tree: dict) -> dict[str, dict]:
    """递归构建 node_id → node 的快速查找索引。"""
    index = {}

    def _walk(node):
        if not isinstance(node, dict):
            return
        nid = node.get("id")
        if nid:
            index[nid] = node
        for child in node.get("children", []):
            _walk(child)

    _walk(tree)
    return index


def _count_elements(node: dict, depth: int = 0) -> int:
    """递归统计交互元素数量（depth ≤ 3）。"""
    if depth > 3:
        return 0
    count = 0
    nt = node.get("type", "")
    if nt in ("TEXT", "INSTANCE", "COMPONENT") and depth > 0:
        count += 1
    for child in node.get("children", []):
        count += _count_elements(child, depth + 1)
    return count


def _format_component_description(comp_info: dict) -> str:
    """格式化组件描述文本。"""
    name = comp_info.get("name", "")
    parts = [name]
    prop_defs = comp_info.get("componentPropertyDefinitions", {})
    if isinstance(prop_defs, dict):
        for k, v in prop_defs.items():
            if isinstance(v, dict) and v.get("type") == "VARIANT":
                opts = v.get("variantOptions", [])
                if opts:
                    parts.append("%s[%s]" % (k, "/".join(str(o) for o in opts[:5])))
    return " — ".join(parts) if len(parts) > 1 else name


def _extract_variant_options(comp_info: dict) -> dict:
    """提取 variant 选项。"""
    options = {}
    prop_defs = comp_info.get("componentPropertyDefinitions", {})
    if isinstance(prop_defs, dict):
        for k, v in prop_defs.items():
            if isinstance(v, dict) and v.get("type") == "VARIANT":
                opts = v.get("variantOptions", [])
                if opts:
                    options[k] = opts
    return options


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()
