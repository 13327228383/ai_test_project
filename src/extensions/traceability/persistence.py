# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
追溯链路引擎 — 持久化层
======================
负责与 trace_link_store（MySQL）交互，以及 JSON 文件的显式导入/导出。
兼容旧入口（_load_cache / _save_cache）。
"""

import json
import logging
from typing import Any

from .models import TraceLink

_log = logging.getLogger("traceability_engine")


class TraceabilityPersistence:
    """追溯链路持久化管理器。"""

    def __init__(self, store=None):
        self._store = store

    # -----------------------------------------------------------------
    # 事实源层：主路径只读写 MySQL；JSON 作为显式导入/导出
    # -----------------------------------------------------------------
    def import_from_json(self, json_path: str, replace: bool = False) -> dict[str, Any]:
        """显式把 ``trace_links.json`` 回填到 MySQL（不作为运行时兜底）。"""
        if self._store is None:
            return {
                "success": False,
                "error": "trace_link_store 不可用",
                "count": 0,
                "path": json_path,
            }
        return self._store.import_from_json(json_path, replace=replace)

    def export_to_json(self, save_path: str) -> dict[str, Any]:
        """显式从 MySQL trace_links 导出 JSON 附件。"""
        if self._store is None:
            return {
                "success": False,
                "error": "trace_link_store 不可用",
                "count": 0,
                "path": save_path,
            }
        return self._store.export_to_json(save_path)

    def load_from_db(self) -> dict[str, TraceLink] | None:
        if self._store is None:
            return None
        try:
            raw = self._store.load_links()
        except Exception as e:
            _log.warning(
                "  [TraceabilityEngine] MySQL 追溯链路读取失败: %s", str(e)[:80]
            )
            return None
        if not raw:
            return None
        parsed = {}
        for rid, item in raw.items():
            try:
                parsed[rid] = TraceLink(
                    req_id=item.get("req_id", rid),
                    module=item.get("module") or "",
                    description=item.get("description") or "",
                    api_endpoints=item.get("api_endpoints") or [],
                    db_tables=item.get("db_tables") or {},
                    figma_pages=item.get("figma_pages") or [],
                    chunk_links=item.get("chunk_links") or [],
                    related_reqs=item.get("related_reqs") or [],
                    confidence=item.get("confidence") or "medium",
                    feature_id=int(item.get("feature_id") or 0),
                )
            except Exception:
                _log.debug(
                    "  [TraceabilityEngine] 解析追溯链路 %s 失败，跳过",
                    rid,
                    exc_info=True,
                )
                continue
        return parsed or None

    def save_to_db(self, links: dict[str, TraceLink]) -> None:
        if self._store is None:
            _log.warning(
                "  [TraceabilityEngine] trace_link_store 不可用，追溯链路未写入 MySQL"
            )
            return
        with guard("save_to_db: payload = { rid: ( link if isinstanc", log=_log, level=logging.WARNING):
            payload = {
                rid: (
                    link
                    if isinstance(link, dict)
                    else {
                        "req_id": link.req_id,
                        "module": link.module,
                        "description": link.description,
                        "api_endpoints": link.api_endpoints,
                        "db_tables": link.db_tables,
                        "figma_pages": link.figma_pages,
                        "chunk_links": link.chunk_links,
                        "related_reqs": link.related_reqs,
                        "confidence": link.confidence,
                        "feature_id": link.feature_id,
                    }
                )
                for rid, link in links.items()
            }
            ok = self._store.upsert_links(payload)
            if not ok:
                _log.warning(
                    "  [TraceabilityEngine] MySQL 追溯链路写入失败: %s",
                    getattr(self._store, "_last_error", "未知错误")[:120],
                )

    def load_cached_links(self) -> dict[str, TraceLink] | None:
        """从事实源（MySQL trace_links 表）加载已保存的追溯链路。

        注意：JSON 文件不再作为运行时兜底；如需导入旧的 trace_links.json，
        请显式调用 ``import_from_json(json_path)``。
        """
        return self.load_from_db()

    def save_trace_links(self, links: dict[str, TraceLink]) -> bool:
        """保存追溯链路：写入 MySQL trace_links 表。

        返回 True 表示成功写入 MySQL；若 store 不可用或写入失败返回 False。
        """
        if self._store is None:
            return False
        self.save_to_db(links)
        return True

    def load_cache(self) -> dict[str, TraceLink] | None:
        """兼容旧入口：直接转发到 MySQL 加载。

        如需显式导入旧 JSON，请使用 ``import_from_json(json_path)``。
        """
        return self.load_from_db()

    def save_cache(self, links: dict[str, TraceLink]) -> None:
        """兼容旧入口：只写入 MySQL。"""
        self.save_to_db(links)
