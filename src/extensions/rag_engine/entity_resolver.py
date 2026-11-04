# -*- coding: utf-8 -*-
"""★ v4: EntityResolver — 源数据精确查询层。

接收 RAG 检索的 entity_refs，直接查询源数据系统获取完整数据。
替代从 chunk text 中反向解析信息的做法。

数据来源:
    - API 端点: rag_chunks 表（meta JSON 含 _structured_params）
    - DB 表结构: 目标被测系统 information_schema（通过 db_agent 实时查询）
    - Figma 节点: figma_nodes 表
"""

import json
import logging
from typing import Any

_log = logging.getLogger(__name__)


class EntityResolver:
    """★ v4: 源数据精确查询层。

    通过 entity_refs 中的 chunk_id 或实体 ID，直接查询源数据系统获取完整数据。
    内置缓存，避免重复查询。rebuild 后需调用 clear_cache()。
    """

    def __init__(self, project: str = None, version_id: str = None):
        self._project = project
        self._version_id = version_id
        self._cache: dict[str, Any] = {}

    def resolve(self, entity_refs: list[dict]) -> dict[str, list[dict]]:
        """主入口：批量解析 entity_refs，返回按类型分组的源数据。

        Args:
            entity_refs: [{"type": "api", "chunk_id": "api:POST:/api/login"}, ...]

        Returns:
            {
                "api_endpoints": [{"method": "POST", "path": "/api/login", ...}, ...],
                "db_tables": [{"name": "users", "columns": [...], ...}, ...],
                "figma_nodes": [{"node_id": "1:2", "name": "Login Button", ...}, ...],
            }
        """
        result = {"api_endpoints": [], "db_tables": [], "figma_nodes": []}
        if not entity_refs:
            return result

        api_refs = [r for r in entity_refs if r.get("type") == "api"]
        db_refs = [r for r in entity_refs if r.get("type") == "db_table"]
        figma_refs = [r for r in entity_refs if r.get("type") == "figma_node"]

        if api_refs:
            result["api_endpoints"] = self._resolve_api_endpoints(api_refs)
        if db_refs:
            result["db_tables"] = self._resolve_db_tables(db_refs)
        if figma_refs:
            result["figma_nodes"] = self._resolve_figma_nodes(figma_refs)

        return result

    def _resolve_api_endpoints(self, refs: list[dict]) -> list[dict]:
        """从 rag_chunks 表查询 API 端点完整 meta（含 _structured_params）。"""
        cache_key = "api_endpoints"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            return [cached[r["chunk_id"]] for r in refs if r.get("chunk_id") in cached]

        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            if not conn:
                return []

            chunk_ids = [r.get("chunk_id") for r in refs if r.get("chunk_id")]
            if not chunk_ids:
                return []

            placeholders = ",".join(["%s"] * len(chunk_ids))
            sql = (
                "SELECT chunk_id, meta FROM rag_chunks "
                "WHERE type='api' AND chunk_id IN ({}) AND is_active=1"
            ).format(placeholders)
            cursor = conn.cursor()
            cursor.execute(sql, tuple(chunk_ids))
            rows = cursor.fetchall()
            cursor.close()

            by_chunk_id = {}
            for row in rows:
                chunk_id = row[0]
                meta_str = row[1]
                try:
                    meta = (
                        json.loads(meta_str) if isinstance(meta_str, str) else meta_str
                    )
                    by_chunk_id[chunk_id] = meta
                except Exception as _e:
                    _log.debug("chunk meta 解析失败: %s", _e, exc_info=True)
                    continue
            self._cache[cache_key] = by_chunk_id

            return [by_chunk_id[cid] for cid in chunk_ids if cid in by_chunk_id]
        except Exception as e:
            _log.warning("[EntityResolver] API 端点查询失败: %s", str(e)[:120])
            return []
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)

    def _resolve_db_tables(self, refs: list[dict]) -> list[dict]:
        """从目标被测系统 DB 实时查询表结构（复用 _parse_db_columns_batch）。"""
        table_names = [
            r.get("id", r.get("chunk_id", "").replace("db:", "", 1))
            for r in refs
            if r.get("id") or r.get("chunk_id")
        ]
        table_names = [tn for tn in table_names if tn]
        if not table_names:
            return []

        cache_key = "db_tables:" + ",".join(sorted(table_names))
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            from src.extensions.rag_engine.chunking_mixin_mixins._parse import (
                ChunkingParseMixin,
            )
            from src.core.config_loader import config as _cfg

            db_type = str(getattr(_cfg, "db_type", "mysql") or "mysql").lower()
            db_name = getattr(_cfg, "db_name", "") or ""

            cols_by_table, pks_by_table = ChunkingParseMixin._parse_db_columns_batch(
                table_names, db_type, db_name
            )

            tables = []
            for tn in table_names:
                cols = cols_by_table.get(tn, [])
                pks = pks_by_table.get(tn, [])
                tables.append(
                    {
                        "name": tn,
                        "columns": [c["COLUMN_NAME"] for c in cols],
                        "col_details": [
                            {
                                "name": c["COLUMN_NAME"],
                                "type": c.get("COLUMN_TYPE") or c.get("DATA_TYPE", ""),
                                "comment": (c.get("COLUMN_COMMENT") or "")[:80],
                                "is_pk": c.get("COLUMN_KEY") == "PRI",
                            }
                            for c in cols
                        ],
                        "primary_keys": pks,
                        "foreign_keys": [],
                    }
                )

            self._cache[cache_key] = tables
            return tables
        except Exception as e:
            _log.warning("[EntityResolver] DB 表结构查询失败: %s", str(e)[:120])
            return []

    def _resolve_figma_nodes(self, refs: list[dict]) -> list[dict]:
        """从 figma_nodes 表查询节点详情。"""
        node_ids = [
            r.get("id", r.get("chunk_id", "").replace("figma_node:", "", 1))
            for r in refs
            if r.get("id") or r.get("chunk_id")
        ]
        node_ids = [nid for nid in node_ids if nid]
        if not node_ids:
            return []

        cache_key = "figma_nodes:" + ",".join(sorted(node_ids))
        if cache_key in self._cache:
            return self._cache[cache_key]

        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            if not conn:
                return []

            placeholders = ",".join(["%s"] * len(node_ids))
            project = self._project or ""
            sql = (
                "SELECT node_id, page_id, parent_node_id, name, node_type, text_content "
                "FROM figma_nodes "
                "WHERE project=%s AND node_id IN ({})"
            ).format(placeholders)
            cursor = conn.cursor()
            cursor.execute(sql, (project, *node_ids))
            rows = cursor.fetchall()
            cursor.close()

            nodes = []
            for row in rows:
                nodes.append(
                    {
                        "node_id": row[0],
                        "page_id": row[1],
                        "parent_node_id": row[2],
                        "name": row[3],
                        "node_type": row[4],
                        "text_content": row[5] or "",
                    }
                )

            self._cache[cache_key] = nodes
            return nodes
        except Exception as e:
            _log.warning("[EntityResolver] Figma 节点查询失败: %s", str(e)[:120])
            return []
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)

    def clear_cache(self):
        """清除缓存（rebuild 后调用）。"""
        self._cache.clear()
        _log.debug("[EntityResolver] 缓存已清除")


# ── 全局实例管理（跨调用缓存 + rebuild 后统一清除）──

_global_resolver: EntityResolver = None


def get_global_resolver(project: str = None, version_id: str = None) -> EntityResolver:
    """获取全局 EntityResolver 实例（跨调用复用缓存）。"""
    global _global_resolver
    if _global_resolver is None:
        _global_resolver = EntityResolver(project=project, version_id=version_id)
    return _global_resolver


def clear_global_cache():
    """清除全局 EntityResolver 缓存（RAG rebuild 后调用）。"""
    global _global_resolver
    if _global_resolver is not None:
        _global_resolver.clear_cache()
    _log.debug("[EntityResolver] 全局缓存已清除")
