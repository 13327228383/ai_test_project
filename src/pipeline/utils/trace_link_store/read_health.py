# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""trace_links 读取 + 健康统计 — 从 trace_link_store.py 拆出，零业务逻辑变更。"""

from typing import Any
import logging
import json as _json

_log = logging.getLogger(__name__)


class _ReadHealthMixin:
    """load_links / count / health_stats — trace_links 只读。"""

    # -----------------------------------------------------------------
    # 读
    # -----------------------------------------------------------------
    def get_link_by_req_id(self, req_id: str) -> dict[str, Any] | None:
        """★ 新增(07-05h): 按 req_id 精确读取单条追溯链路（带版本隔离）。

        Args:
            req_id: 需求ID

        Returns:
            链路字典（含 api_endpoints/db_tables/figma_pages/related_reqs），
            未找到返回 None
        """
        if not req_id:
            return None
        try:
            from src.core.config_loader import config
            from src.core.versioning import current_version_id

            project = getattr(config, "project_name", "") or ""
            version_id = current_version_id(project)
        except Exception as _e:
            _log.debug("[read_health] get_link_by_req_id 异常: %s", _e, exc_info=True)
            return None

        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            row = c.execute(
                "SELECT * FROM trace_links "
                "WHERE project=%s AND version_id=%s AND req_id=%s LIMIT 1",
                (project, version_id, req_id),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            # ★ 07-06 单一事实源：资源详情从 trace_link_chunks + rag_chunks 读取
            chunk_links, details = self._load_chunk_links(
                c, project, version_id, req_id
            )
            return {
                "req_id": str(item.get("req_id", "") or ""),
                "module": str(item.get("module", "") or ""),
                "description": str(item.get("description", "") or ""),
                "api_endpoints": details.get("api", []),
                "db_tables": details.get("db_table", {}),
                "figma_pages": details.get("figma_page", []),
                "related_reqs": self._from_json(item.get("related_reqs_json"), []),
                "confidence": str(item.get("confidence", "medium") or "medium"),
                "feature_id": int(item.get("feature_id") or 0),
                "chunk_links": chunk_links,
            }
        except Exception as e:
            self._last_error = str(e)
            _log.debug("[TraceLinkStore] get_link_by_req_id 失败: %s", str(e)[:120])
            return None
        finally:
            if c:
                with guard("get_link_by_req_id: c.close()", log=_log):
                    c.close()

    def load_links(self) -> dict[str, dict[str, Any]]:
        try:
            from src.core.config_loader import config
            from src.core.versioning import current_version_id

            project = getattr(config, "project_name", "") or ""
            version_id = current_version_id(project)
        except Exception as _e:
            _log.debug("[read_health] load_links 异常: %s", _e, exc_info=True)
            project = ""
            version_id = 0
        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            rows = c.execute(
                "SELECT * FROM trace_links WHERE project=%s AND version_id=%s ORDER BY req_id",
                (project, version_id),
            ).fetchall()
            self._last_error = ""

            # ★ 07-06 单一事实源：批量读取 trace_link_chunks 和 rag_chunks
            req_ids = [str(dict(row).get("req_id", "")) for row in (rows or [])]
            all_chunk_links = self._load_all_chunk_links(
                c, project, version_id, req_ids
            )
            all_chunk_ids = []
            for links in all_chunk_links.values():
                all_chunk_ids.extend([l["chunk_id"] for l in links])
            chunk_details = self._load_chunk_details(
                c, project, list(set(all_chunk_ids))
            )

            result: dict[str, dict[str, Any]] = {}
            for row in rows or []:
                item = dict(row)
                rid = str(item.get("req_id", "")).strip()
                if not rid:
                    continue
                chunk_links = all_chunk_links.get(rid, [])
                details = self._group_chunk_details(chunk_links, chunk_details)
                result[rid] = {
                    "req_id": rid,
                    "module": str(item.get("module", "") or ""),
                    "description": str(item.get("description", "") or ""),
                    "api_endpoints": details.get("api", []),
                    "db_tables": details.get("db_table", {}),
                    "figma_pages": details.get("figma_page", []),
                    "related_reqs": self._from_json(item.get("related_reqs_json"), []),
                    "confidence": str(item.get("confidence", "medium") or "medium"),
                    "feature_id": int(item.get("feature_id") or 0),
                    "chunk_links": chunk_links,
                }
            return result
        except Exception as e:
            self._last_error = str(e)
            _log.debug("  [TraceLinkStore] trace_links 读取失败: %s", str(e)[:120])
            return {}
        finally:
            if c:
                with guard("load_links: c.close()", log=_log):
                    c.close()

    def count(self) -> int:
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        except Exception as _e:
            _log.debug("[read_health] count 异常: %s", _e, exc_info=True)
            project = ""
        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            row = c.execute(
                "SELECT COUNT(*) AS cnt FROM trace_links WHERE project=%s",
                (project,),
            ).fetchone()
            if row is None:
                return 0
            cnt = row["cnt"] if isinstance(row, dict) else (row[0] if row else 0)
            return int(cnt or 0)
        except Exception as e:
            self._last_error = str(e)
            return 0
        finally:
            if c:
                with guard("count: c.close()", log=_log):
                    c.close()

    # -----------------------------------------------------------------
    # 一致性健康检查：供 consistency_checker --db-health 调用
    # -----------------------------------------------------------------
    def health_stats(self, project: str | None = None) -> dict[str, Any]:
        """返回 trace_links 表的只读健康统计。

        若未传入 project 参数，则从全局 config.project_name 读取；
        若仍无法获取，则使用空字符串以匹配现有 upsert 行为。
        """
        if project is None:
            try:
                from src.core.config_loader import config

                project = getattr(config, "project_name", "") or ""
            except Exception as _e:
                _log.debug("[read_health] health_stats 异常: %s", _e, exc_info=True)
                project = ""
        stats: dict[str, Any] = {
            "trace_link_count": 0,
            "high_confidence": 0,
            "medium_confidence": 0,
            "low_confidence": 0,
            "has_api_endpoints": 0,
            "has_db_tables": 0,
            "has_figma_pages": 0,
            "error": None,
            "project": str(project),
        }
        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            total_row = c.execute(
                "SELECT COUNT(*) AS cnt FROM trace_links WHERE project=%s",
                (project,),
            ).fetchone()
            stats["trace_link_count"] = int(
                (
                    total_row["cnt"]
                    if isinstance(total_row, dict)
                    else (total_row[0] if total_row else 0)
                )
                or 0
            )
            for conf in ("high", "medium", "low"):
                r = c.execute(
                    "SELECT COUNT(*) AS cnt FROM trace_links WHERE project=%s AND confidence=%s",
                    (project, conf),
                ).fetchone()
                key = "%s_confidence" % conf
                stats[key] = int(
                    (r["cnt"] if isinstance(r, dict) else (r[0] if r else 0)) or 0
                )
            # ★ 07-06 单一事实源：按 trace_link_chunks 统计资源覆盖（与 trace_link_count 同维度：按 project）
            for ctype, key in (
                ("api", "has_api_endpoints"),
                ("db_table", "has_db_tables"),
                ("figma_page", "has_figma_pages"),
            ):
                r = c.execute(
                    "SELECT COUNT(DISTINCT req_id) AS cnt FROM trace_link_chunks "
                    "WHERE project=%s AND chunk_type=%s",
                    (project, ctype),
                ).fetchone()
                stats[key] = int(
                    (r["cnt"] if isinstance(r, dict) else (r[0] if r else 0)) or 0
                )
        except Exception as e:
            self._last_error = str(e)
            stats["error"] = "health_stats_failed: %s" % str(e)[:200]
        finally:
            if c:
                with guard("health_stats: c.close()", log=_log):
                    c.close()
        return stats

    # -----------------------------------------------------------------
    # 内部辅助：trace_link_chunks + rag_chunks 读取
    # -----------------------------------------------------------------
    def _load_chunk_links(self, c, project: str, version_id: int, req_id: str) -> tuple:
        """读取单条需求的 trace_link_chunks 及其 rag_chunks 详情。"""
        try:
            rows = (
                c.execute(
                    "SELECT chunk_type, chunk_id, rel_type, confidence FROM trace_link_chunks "
                    "WHERE project=%s AND version_id=%s AND req_id=%s",
                    (project, version_id, req_id),
                ).fetchall()
                or []
            )
        except Exception as e:
            _log.debug("[read_health] _load_chunk_links 失败: %s", str(e)[:80])
            return [], {}
        chunk_links = [dict(r) for r in rows]
        chunk_ids = list({l["chunk_id"] for l in chunk_links})
        details = self._load_chunk_details(c, project, chunk_ids)
        return chunk_links, self._group_chunk_details(chunk_links, details)

    def _load_all_chunk_links(
        self, c, project: str, version_id: int, req_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """批量读取多个需求的 trace_link_chunks。"""
        result: dict[str, list[dict[str, Any]]] = {}
        if not req_ids:
            return result
        with guard("_load_all_chunk_links: placeholders = ','.join(['%s'] * len", log=_log):
            placeholders = ",".join(["%s"] * len(req_ids))
            rows = (
                c.execute(
                    f"SELECT req_id, chunk_type, chunk_id, rel_type, confidence FROM trace_link_chunks "
                    f"WHERE project=%s AND version_id=%s AND req_id IN ({placeholders})",
                    (project, version_id, *req_ids),
                ).fetchall()
                or []
            )
            for r in rows:
                item = dict(r)
                rid = item.get("req_id")
                if not rid:
                    continue
                result.setdefault(str(rid), []).append(item)
        return result

    def _load_chunk_details(
        self, c, project: str, chunk_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """批量从 rag_chunks 读取 chunk 详情。"""
        result: dict[str, dict[str, Any]] = {}
        if not chunk_ids or not project:
            return result
        with guard("_load_chunk_details: batch_size = 200", log=_log):
            # 分批查询，避免 IN 列表过大
            batch_size = 200
            for i in range(0, len(chunk_ids), batch_size):
                batch = chunk_ids[i : i + batch_size]
                placeholders = ",".join(["%s"] * len(batch))
                rows = (
                    c.execute(
                        f"SELECT chunk_id, type, meta FROM rag_chunks "
                        f"WHERE scope_type='project' AND scope_id=%s AND chunk_id IN ({placeholders})",
                        (project, *batch),
                    ).fetchall()
                    or []
                )
                for r in rows:
                    item = dict(r)
                    cid = item.get("chunk_id")
                    if not cid:
                        continue
                    meta = item.get("meta")
                    meta_dict = {}
                    if isinstance(meta, str) and meta:
                        try:
                            meta_dict = _json.loads(meta)
                        except Exception as _e:
                            _log.debug("meta JSON 解析失败: %s", _e, exc_info=True)
                    elif isinstance(meta, dict):
                        meta_dict = meta
                    result[str(cid)] = {
                        "chunk_id": cid,
                        "chunk_type": item.get("type"),
                        "meta": meta_dict,
                    }
        return result

    def _group_chunk_details(
        self,
        chunk_links: list[dict[str, Any]],
        chunk_details: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """把 chunk_links + rag_chunks 详情反解为前端/下游需要的资源结构。"""
        api_endpoints: list[dict[str, Any]] = []
        db_tables: dict[str, list[str]] = {}
        figma_pages: list[str] = []

        for cl in chunk_links:
            ctype = cl.get("chunk_type")
            cid = cl.get("chunk_id")
            detail = chunk_details.get(cid, {})
            meta = detail.get("meta") or {}

            if ctype == "api":
                api_endpoints.append(
                    {
                        "method": meta.get("method", ""),
                        "path": meta.get("path", ""),
                        "summary": meta.get("summary") or meta.get("title", ""),
                    }
                )
            elif ctype == "db_table":
                tname = meta.get("name") or cid.replace("db:", "", 1)
                cols = meta.get("columns") or []
                if isinstance(cols, str):
                    try:
                        cols = _json.loads(cols)
                    except Exception:
                        _log.warning("JSON解析columns失败", exc_info=True)
                        cols = []
                db_tables[str(tname)] = cols or []
            elif ctype == "figma_page":
                name = meta.get("name") or cid
                figma_pages.append(str(name))

        return {
            "api": api_endpoints,
            "db_table": db_tables,
            "figma_page": figma_pages,
        }
