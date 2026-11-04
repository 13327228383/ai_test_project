# -*- coding: utf-8 -*-
from __future__ import annotations

"""
_FigmaCacheReadMixin — 读取缓存方法：get, get_full_file, get_full_file_with_meta,
fetch_figma_nodes, list_cached_nodes, has_file_cache。
"""

import json
from typing import Any
import logging

_log = logging.getLogger(__name__)


class _FigmaCacheReadMixin:
    """读取缓存方法 mixin。"""

    def get(
        self, file_key: str, node_id: str = "", depth: int = 0
    ) -> dict[str, Any] | None:
        """读取缓存，返回 dict 或 None。"""
        self._ensure_conn()
        project, version = self._scope()
        key = self._make_key(file_key, node_id, depth)
        with self._lock:
            row = self._conn.execute(
                "SELECT response_json FROM %s WHERE cache_key = %%s AND project = %%s AND version_label = %%s"
                % self._TABLE,
                (key, project, version),
            ).fetchone()
        if row is not None:
            try:
                return json.loads(row["response_json"])
            except json.JSONDecodeError as _e:
                _log.debug("[figma_cache] 缓存 JSON 解析失败(§5): %s", _e)
                return None
        return None

    def get_full_file(self, file_key: str) -> dict[str, Any] | None:
        """获取全量文件缓存（优先 depth=0，回退 depth=2）。"""
        for depth in (0, 2):
            data = self.get(file_key, node_id="", depth=depth)
            if data is not None:
                return data
        return None

    def get_full_file_with_meta(self, file_key: str) -> dict[str, Any] | None:
        """获取全量文件缓存 + 时间戳元信息（优先 depth=0，回退 depth=2）。"""
        self._ensure_conn()
        project, version = self._scope()
        for depth in (0, 2):
            key = self._make_key(file_key, "", depth)
            with self._lock:
                row = self._conn.execute(
                    "SELECT response_json, created_at, updated_at FROM %s WHERE cache_key = %%s"
                    " AND project = %%s AND version_label = %%s" % self._TABLE,
                    (key, project, version),
                ).fetchone()
            if row is not None:
                break
        if row is None:
            return None
        try:
            data = json.loads(row["response_json"])
            data["_cache_created"] = row["created_at"]
            data["_cache_updated"] = row["updated_at"]
            return data
        except json.JSONDecodeError as _e:
            _log.debug("[figma_cache] 缓存数据解析失败(§5): %s", _e)
            return None

    def fetch_figma_nodes(
        self, file_key: str = None, node_ids: list[str] = None, depth: int = 2
    ) -> dict[str, Any]:
        """按 file_key + 节点列表查询缓存（不调 Figma API，仅读本地 MySQL）。

        用于 step1.py 在 UI 层需要展示 Figma 节点缓存的场景，统一返回 dict
        (包含: file_key, depth, nodes: {node_id: parsed_json}, missing: [node_id...])
        """
        self._ensure_conn()
        project, version = self._scope()
        if file_key is None:
            from src.core.config_loader import config

            file_key = getattr(config, "figma_file_key", None) or ""
        result = {
            "file_key": file_key,
            "depth": depth,
            "nodes": {},
            "missing": [],
            "cached_at": None,
        }
        node_ids = list(node_ids) if node_ids else []
        try:
            with self._lock:
                if node_ids:
                    placeholders = ",".join(["%s"] * len(node_ids))
                    rows = self._conn.execute(
                        "SELECT cache_key, node_id, response_json, updated_at "
                        "FROM %s WHERE project = %%s AND version_label = %%s AND file_key = %%s AND depth = %%s "
                        "AND node_id IN (%s)" % (self._TABLE, placeholders),
                        (project, version, file_key, depth) + tuple(node_ids),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        "SELECT cache_key, node_id, response_json, updated_at "
                        "FROM %s WHERE project = %%s AND version_label = %%s AND file_key = %%s AND depth = %%s"
                        % self._TABLE,
                        (project, version, file_key, depth),
                    ).fetchall()
            if not rows:
                result["missing"] = list(node_ids)
                return result
            fetched = set()
            latest = None
            for r in rows:
                nid = r.get("node_id", "")
                try:
                    parsed = json.loads(r.get("response_json") or "{}")
                except Exception as _e:
                    _log.debug("[read] 非关键异常: %s", _e, exc_info=True)
                    parsed = {}
                result["nodes"][nid] = parsed
                fetched.add(nid)
                updated_at = r.get("updated_at")
                if latest is None or (updated_at and updated_at > latest):
                    latest = updated_at
            result["missing"] = [n for n in node_ids if n not in fetched]
            # datetime -> ISO string（便于 JSON 序列化）
            if latest is not None:
                try:
                    if hasattr(latest, "isoformat"):
                        result["cached_at"] = latest.isoformat()
                    else:
                        result["cached_at"] = str(latest)
                except Exception:
                    result["cached_at"] = str(latest)
            return result
        except Exception as e:
            _log.warning("[FigmaCache] fetch_figma_nodes 失败: %s", e)
            result["missing"] = list(node_ids or [])
            return result

    def list_cached_nodes(self, file_key: str) -> list[tuple]:
        """
        列出某个文件下所有已缓存的节点数据。
        返回: [(node_id, response_dict), ...]，不包含全量文件条目。
        """
        self._ensure_conn()
        project, version = self._scope()
        with self._lock:
            rows = self._conn.execute(
                "SELECT node_id, response_json FROM %s "
                "WHERE project = %%s AND version_label = %%s AND file_key = %%s AND node_id != ''"
                % self._TABLE,
                (project, version, file_key),
            ).fetchall()
        results = []
        for row in rows:
            try:
                data = json.loads(row["response_json"])
                results.append((row["node_id"], data))
            except json.JSONDecodeError as _e:
                _log.debug("[figma_cache] JSON 解析失败跳过节点(§5): %s", _e)
        return results

    def has_file_cache(self, file_key: str) -> bool:
        """检查是否存在该文件的全量缓存。"""
        self._ensure_conn()
        project, version = self._scope()
        key = self._make_key(file_key, node_id="", depth=0)
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM %s WHERE cache_key = %%s AND project = %%s AND version_label = %%s"
                % self._TABLE,
                (key, project, version),
            ).fetchone()
        return row is not None
