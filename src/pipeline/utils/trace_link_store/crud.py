# -*- coding: utf-8 -*-
"""trace_links 写入：upsert / clear — 从 trace_link_store.py 拆出，零业务逻辑变更。"""

from datetime import datetime
from src.core.guarded import guard
from typing import Any
import logging

_log = logging.getLogger(__name__)


class _CrudMixin:
    """upsert_links / clear_project_links — trace_links 写入。"""

    # -----------------------------------------------------------------
    # 写
    # -----------------------------------------------------------------
    def upsert_links(self, links: dict[str, Any]) -> bool:
        """把追溯链路写入 MySQL。links 的值可以是 TraceLink dataclass 或 dict。"""
        if not links:
            return True
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
            version_label = str(getattr(config, "project_version", "") or "")
        except Exception as _e:
            _log.debug("[crud] upsert_links 异常: %s", _e, exc_info=True)
            project = ""
            version_label = ""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)

            for rid, link in links.items():
                if not isinstance(link, dict):
                    link = self._link_to_dict(link, rid)

                req_id = str(link.get("req_id", rid) or rid).strip()
                if not req_id:
                    continue

                module = str(link.get("module", "") or "")
                description = str(link.get("description", "") or "")
                related_reqs = link.get("related_reqs", []) or []
                confidence = str(link.get("confidence", "medium") or "medium")
                feature_id = int(link.get("feature_id") or 0)

                # 获取当前活跃版本ID
                try:
                    from src.core.versioning import current_version_id

                    version_id = current_version_id(project)
                except Exception as _e:
                    _log.debug("[crud] 非关键异常: %s", _e, exc_info=True)
                    version_id = 0

                # ★ 先查已有记录 created_at（避免 REPLACE INTO 内 SELECT 同表触发 1093 错误）
                persistent_created = (
                    self._get_created_at(c, req_id, project, version_id) or now
                )

                # ★ 07-06 单一事实源：trace_links 只保留需求级元数据，资源详情走 trace_link_chunks
                payload = {
                    "req_id": req_id,
                    "module": module,
                    "description": description,
                    "related_reqs": related_reqs,
                    "confidence": confidence,
                    "feature_id": feature_id,
                }

                c.execute(
                    """
                    REPLACE INTO trace_links(
                        req_id, project, version_id, feature_id, module, description,
                        related_reqs_json, confidence, payload_json,
                        created_at, updated_at
                    )
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        req_id,
                        project,
                        version_id,
                        feature_id,
                        module,
                        description,
                        self._to_json(related_reqs),
                        confidence,
                        self._to_json(payload),
                        persistent_created,
                        now,
                    ),
                )

                # ★ 07-06 单一事实源：把资源关联写入 trace_link_chunks（同一连接，统一 commit）
                chunk_links = self._resolve_chunk_links(
                    c, project, version_id, version_label, link, confidence
                )
                self._write_trace_link_chunks(
                    c, req_id, project, version_id, chunk_links
                )

            c.commit()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.warning(
                "  [TraceLinkStore] trace_links 写入失败: %s",
                str(e)[:120],
                exc_info=True,
            )
            with guard("upsert_links: if c: c.rollback()", log=_log):
                if c:
                    c.rollback()
            return False
        finally:
            if c:
                with guard("upsert_links: c.close()", log=_log):
                    c.close()

    def clear_project_links(self) -> bool:
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        except Exception as _e:
            _log.debug("[crud] clear_project_links 异常: %s", _e, exc_info=True)
            project = ""
        from src.core.versioning import current_version_id

        active_version_id = current_version_id(project) if project else 0
        c = None
        try:
            c = self._get_db()
            self._ensure_tables(c)
            # ★ 07-06 单一事实源：同时清理 trace_links 和 trace_link_chunks
            c.execute(
                "DELETE FROM trace_links WHERE project=%s AND version_id=%s",
                (project, active_version_id),
            )
            c.execute(
                "DELETE FROM trace_link_chunks WHERE project=%s AND version_id=%s",
                (project, active_version_id),
            )
            c.commit()
            self._last_error = ""
            return True
        except Exception as e:
            self._last_error = str(e)
            _log.debug("  [TraceLinkStore] trace_links 清空失败: %s", str(e)[:120])
            return False
        finally:
            if c:
                with guard("clear_project_links: c.close()", log=_log):
                    c.close()

    # -----------------------------------------------------------------
    # 内部辅助
    # -----------------------------------------------------------------
    def _link_to_dict(self, link: Any, rid: str) -> dict[str, Any]:
        """TraceLink dataclass / 其他对象转 dict；兼容旧 trace_links.json 格式。"""
        if isinstance(link, dict):
            d = dict(link)
        else:
            d = getattr(link, "__dict__", {}) or {}
            if not d:
                for k in (
                    "module",
                    "description",
                    "api_endpoints",
                    "db_tables",
                    "figma_pages",
                    "chunk_links",
                    "related_reqs",
                    "confidence",
                    "feature_id",
                ):
                    d[k] = getattr(link, k, "")
        d["req_id"] = d.get("req_id") or rid
        return d

    def _get_created_at(self, c, req_id: str, project: str, version_id: int) -> str:
        try:
            row = c.execute(
                "SELECT created_at FROM trace_links WHERE req_id=%s AND project=%s AND version_id=%s",
                (req_id, project, version_id),
            ).fetchone()
            return row.get("created_at") if row else ""
        except Exception as e:
            _log.warning("追溯链路created_at查询异常: %s", e, exc_info=True)
            return ""

    def _resolve_chunk_links(
        self,
        c,
        project: str,
        version_id: int,
        version_label: str,
        link: dict[str, Any],
        default_confidence: str,
    ) -> list[dict[str, Any]]:
        """把 AI 输出的自然语言资源描述解析为 rag_chunks.chunk_id 列表。"""
        chunk_links: list[dict[str, Any]] = []
        seen: set = set()

        # 1) API endpoints: api:{method}:{path}
        for ep in link.get("api_endpoints") or []:
            if not isinstance(ep, dict):
                continue
            method = str(ep.get("method", "") or "").upper()
            path = str(ep.get("path", "") or "")
            if not method or not path:
                continue
            chunk_id = f"api:{method}:{path}"
            if chunk_id in seen:
                continue
            if self._chunk_exists(c, project, "api", chunk_id):
                seen.add(chunk_id)
                chunk_links.append(
                    {
                        "chunk_type": "api",
                        "chunk_id": chunk_id,
                        "rel_type": "ai",
                        "confidence": ep.get("confidence") or default_confidence,
                    }
                )

        # 2) DB tables: db:{table_name}
        db_tables = link.get("db_tables") or {}
        if isinstance(db_tables, dict):
            for tname in db_tables:
                tname = str(tname or "").strip()
                if not tname:
                    continue
                chunk_id = f"db:{tname}"
                if chunk_id in seen:
                    continue
                if self._chunk_exists(c, project, "db_table", chunk_id):
                    seen.add(chunk_id)
                    chunk_links.append(
                        {
                            "chunk_type": "db_table",
                            "chunk_id": chunk_id,
                            "rel_type": "ai",
                            "confidence": default_confidence,
                        }
                    )

        # 3) Figma pages: figma_page:{page_id}（按 page_name 查系统表，用户确认可硬编码）
        for p in link.get("figma_pages") or []:
            page_name = p.get("name") if isinstance(p, dict) else str(p or "")
            if not page_name:
                continue
            page_id = self._resolve_figma_page_id(c, project, version_label, page_name)
            if not page_id:
                continue
            chunk_id = f"figma_page:{page_id}"
            if chunk_id in seen:
                continue
            if self._chunk_exists(c, project, "figma_page", chunk_id):
                seen.add(chunk_id)
                chunk_links.append(
                    {
                        "chunk_type": "figma_page",
                        "chunk_id": chunk_id,
                        "rel_type": "ai",
                        "confidence": default_confidence,
                    }
                )

        return chunk_links

    def _write_trace_link_chunks(
        self,
        c,
        req_id: str,
        project: str,
        version_id: int,
        chunk_links: list[dict[str, Any]],
    ) -> None:
        """同一连接下：先删除旧关联，再写入新关联。"""
        if not chunk_links:
            return
        c.execute(
            "DELETE FROM trace_link_chunks WHERE req_id=%s AND project=%s AND version_id=%s",
            (req_id, project, version_id),
        )
        for cl in chunk_links:
            c.execute(
                """
                INSERT INTO trace_link_chunks (
                    req_id, project, version_id, chunk_type, chunk_id, rel_type, confidence, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    req_id,
                    project,
                    version_id,
                    str(cl.get("chunk_type", "") or "").strip(),
                    str(cl.get("chunk_id", "") or "").strip(),
                    str(cl.get("rel_type", "matched") or "matched").strip(),
                    str(cl.get("confidence", "medium") or "medium").strip(),
                ),
            )

    def _chunk_exists(self, c, project: str, chunk_type: str, chunk_id: str) -> bool:
        """检查 rag_chunks 中是否存在指定 chunk。"""
        try:
            row = c.execute(
                "SELECT 1 FROM rag_chunks WHERE scope_type='project' AND scope_id=%s "
                "AND type=%s AND chunk_id=%s AND is_active=1 LIMIT 1",
                (project, chunk_type, chunk_id),
            ).fetchone()
            return bool(row)
        except Exception as e:
            _log.debug("[crud] _chunk_exists 失败: %s", str(e)[:80])
            return False

    def _resolve_figma_page_id(
        self, c, project: str, version_label: str, page_name: str
    ) -> str:
        """按 page_name 从 figma_pages 系统表查 page_id。"""
        if not project or not page_name:
            return ""
        with guard("_resolve_figma_page_id: row = c.execute( 'SELECT page_id FRO", log=_log):
            # 用户确认当前系统表可硬编码；优先按 version_label 精确匹配，退化到项目级任意版本
            row = c.execute(
                "SELECT page_id FROM figma_pages WHERE project=%s AND version_label=%s AND name=%s LIMIT 1",
                (project, version_label, page_name),
            ).fetchone()
            if row and row.get("page_id"):
                return str(row.get("page_id"))
            row = c.execute(
                "SELECT page_id FROM figma_pages WHERE project=%s AND name=%s LIMIT 1",
                (project, page_name),
            ).fetchone()
            if row and row.get("page_id"):
                return str(row.get("page_id"))
        return ""
