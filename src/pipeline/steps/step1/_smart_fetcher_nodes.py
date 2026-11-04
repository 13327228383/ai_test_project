# -*- coding: utf-8 -*-
"""
smart_figma_fetcher 的节点提取与变更检测子模块
===============================================
零业务逻辑变更，仅做物理代码搬迁。
"""

from typing import Any

from src.pipeline.steps.step1.figma_hasher import compute_node_hash


# =============================================================================
# 从全量文档缓存提取节点
# =============================================================================


def extract_nodes_from_full_doc(
    client_cache,
    file_key: str,
    node_ids: list[str],
    node_hashes: dict[str, str],
) -> dict[str, Any]:
    """从全量文档缓存中一次遍历提取所有指定节点。"""
    try:
        full_data = client_cache.get_full_file(file_key)
        if full_data is None:
            return {"success": False, "error": "全量文档缓存未找到"}

        document = full_data.get("document", {})
        target_set = set(node_ids)
        all_nodes = {}
        changed_ids, new_ids, unchanged_ids = [], [], []

        def collect_nodes(tree):
            if not isinstance(tree, dict) or not target_set:
                return
            tid = tree.get("id")
            if tid in target_set:
                all_nodes[tid] = tree
                node_hash = compute_node_hash(tree)
                old_hash = node_hashes.get(tid)
                if old_hash is None:
                    new_ids.append(tid)
                elif node_hash != old_hash:
                    changed_ids.append(tid)
                else:
                    unchanged_ids.append(tid)
                target_set.discard(tid)
            for child in tree.get("children", []):
                collect_nodes(child)

        collect_nodes(document)

        return {
            "success": True,
            "nodes": all_nodes,
            "changed_ids": changed_ids,
            "new_ids": new_ids,
            "unchanged_ids": unchanged_ids,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# 从内存 document dict 提取节点
# =============================================================================


def extract_nodes_from_document(
    document: dict,
    node_ids: list[str],
    node_hashes: dict[str, str],
) -> dict[str, Any]:
    """从内存中的全量文档 dict 一次遍历提取所有指定节点（无需额外 API 调用）。"""
    if not document or not node_ids:
        return {"nodes": {}, "changed_ids": [], "new_ids": [], "unchanged_ids": []}
    target_set = set(node_ids)
    all_nodes = {}
    changed_ids, new_ids, unchanged_ids = [], [], []

    def collect_nodes(tree):
        if not isinstance(tree, dict) or not target_set:
            return
        tid = tree.get("id")
        if tid in target_set:
            all_nodes[tid] = tree
            node_hash = compute_node_hash(tree)
            old_hash = node_hashes.get(tid)
            if old_hash is None:
                new_ids.append(tid)
            elif node_hash != old_hash:
                changed_ids.append(tid)
            else:
                unchanged_ids.append(tid)
            node_hashes[tid] = node_hash
            target_set.discard(tid)
        for child in tree.get("children", []):
            collect_nodes(child)

    collect_nodes(document)

    return {
        "success": True,
        "nodes": all_nodes,
        "changed_ids": changed_ids,
        "new_ids": new_ids,
        "unchanged_ids": unchanged_ids,
    }


# =============================================================================
# 仅从 MySQL 缓存读取节点
# =============================================================================


def batch_fetch_nodes_cached(
    client_cache,
    file_key: str,
    node_ids: list[str],
    node_hashes: dict[str, str],
) -> dict[str, Any]:
    """仅从 MySQL 缓存读取节点数据，不调 Figma API。"""
    if not node_ids:
        return {
            "success": True,
            "nodes": {},
            "changed_ids": [],
            "new_ids": [],
            "unchanged_ids": [],
        }

    all_nodes = {}
    cached_entries = client_cache.list_cached_nodes(file_key)
    cached_map = {nid: data for nid, data in cached_entries}

    missing_count = 0
    for node_id in node_ids:
        if node_id in cached_map:
            node_info = cached_map[node_id]
            all_nodes[node_id] = (
                node_info if isinstance(node_info, dict) else {"raw": node_info}
            )
        else:
            missing_count += 1

    if missing_count == len(node_ids) and len(node_ids) > 0:
        return {"success": False, "error": "所有节点均无缓存数据"}

    changed_ids, new_ids, unchanged_ids = [], [], []
    for node_id, node_info in all_nodes.items():
        node_hash = compute_node_hash(node_info)
        old_hash = node_hashes.get(node_id)
        if old_hash is None:
            new_ids.append(node_id)
        elif node_hash != old_hash:
            changed_ids.append(node_id)
        else:
            unchanged_ids.append(node_id)

    return {
        "success": True,
        "nodes": all_nodes,
        "changed_ids": changed_ids,
        "new_ids": new_ids,
        "unchanged_ids": unchanged_ids,
    }
