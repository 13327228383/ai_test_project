# -*- coding: utf-8 -*-
"""
加载 mixin：ChromaDB / MySQL 索引加载、清空
"""

import json
from src.core.guarded import guard
import logging

_log = logging.getLogger("rag_engine")


class PersistenceLoadMixin:
    """索引加载、统计、清空相关方法。"""

    def _load_chunks(self, project: str = "", version_label: str = "") -> None:
        """从 MySQL rag_chunks 加载「项目 + 版本」分区的有效索引（is_active=1）。

        project/version_label 由调用方显式传入时以其为准（build_index 在入口线程
        解析一次），避免在本方法内二次推导上下文。

        版本是分区的必需维度：rag_chunks 的稳定键在项目 + 版本内唯一，跨版本读取
        会把别的版本的同名 chunk 当成"旧数据"合并回来。版本无法确定时只告警并
        返回空索引，不做跨版本兜底。
        """
        project = project or self._get_project_name()
        version_label = version_label or self._get_project_version()
        if not project or not version_label:
            _log.warning(
                "[RagEngine] 项目(%s)或版本(%s)上下文缺失，跳过从 MySQL 加载",
                project or "?",
                version_label or "?",
            )
            self._chunks = []
            return
        scope_type, scope_id = self._project_scope(project)
        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            # ★ 2026-06-22：建表已统一由 src/core/schema.sql 负责，此处不再 CREATE TABLE / ALTER
            max_load = getattr(self, "_MAX_LOAD_CHUNKS", 10000)
            rows = conn.execute(
                "SELECT chunk_id, type, text, meta, keywords, vector_blob, vector_json, "
                "embed_dim, embed_model, chapter_hash "
                "FROM rag_chunks WHERE scope_type = %s AND scope_id = %s "
                "AND version_label = %s AND is_active = 1 LIMIT %s",
                (scope_type, scope_id, version_label, max_load),
            ).fetchall()
            # ★ 嵌入模型一致性检测（无硬编码兜底，config 已走 user_credentials）
            try:
                from src.core.config_loader import config as _cfg

                current_model = str(getattr(_cfg, "ollama_embed_model", "") or "")
            except Exception as _ce:
                _log.debug("[load] embed_model配置失败(用空): %s", str(_ce)[:50])
                current_model = ""
            stored_models = set()
            for row in rows:
                em = (row.get("embed_model") or "").strip()
                if em and (row.get("embed_dim") or 0) > 0:
                    stored_models.add(em)
            if stored_models and current_model not in stored_models:
                _log.warning(
                    "[RagEngine] 嵌入模型不一致！当前=%s 库中=%s，清空向量仅保留 BM25 检索",
                    current_model,
                    ",".join(sorted(stored_models)),
                )
                skip_vectors = True
            else:
                skip_vectors = False
            self._chunks = []
            has_vectors = False
            for row in rows:
                chunk = {
                    "chunk_id": row["chunk_id"],
                    "type": row["type"],
                    "text": row["text"],
                    "meta": row.get("meta", "") or "",
                    "chapter_hash": row.get("chapter_hash", "") or "",
                }
                try:
                    kw = row.get("keywords")
                    chunk["keywords"] = json.loads(kw) if kw else []
                except Exception as _je:
                    _log.debug("[load] keywords JSON解析失败(用[]): %s", str(_je)[:50])
                    chunk["keywords"] = []
                vec = None
                if not skip_vectors:
                    if row.get("vector_blob"):
                        try:
                            vec = self._blob_to_vector(
                                row["vector_blob"], dim=row.get("embed_dim", 0)
                            )
                        except Exception as _pass_e:
                            _log.warning("非关键异常: %s", _pass_e, exc_info=True)
                    if not vec and row.get("vector_json"):
                        try:
                            vec = json.loads(row["vector_json"])
                        except Exception as _pass_e:
                            _log.warning("非关键异常: %s", _pass_e, exc_info=True)
                if vec:
                    chunk["_vector"] = vec
                    has_vectors = True
                self._chunks.append(chunk)
            self._use_embeddings = has_vectors
            self._detected_metric = None
            self._build_bm25_index()
            _log.info(
                "[RagEngine] 已从 MySQL 加载: %d chunks (embedding=%s, project=%s, version=%s)",
                len(self._chunks),
                "✓" if has_vectors else "✗",
                project,
                version_label,
            )
        except Exception as e:
            _log.warning("[RagEngine] MySQL 加载失败: %s", e)
            self._chunks = []
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)

    def _count_chunks(self, project: str = "", version_label: str = "") -> int:
        """统计「项目 + 版本」分区的有效 chunk 数量（is_active=1）。"""
        stats = self._get_chunk_stats_by_type(project=project, version_label=version_label)
        return stats.get("total", 0)

    def _get_chunk_stats_by_type(
        self, project: str = "", version_label: str = ""
    ) -> dict:
        """★ v3: 从 MySQL rag_chunks 统计各类型 chunk 数量 + 字符数 + 字节数 + 向量数。
        ★ v6: 增加当前版本 chunks 统计（current_version_chunks），用于前端展示"当前版本/总计"。

        传 version_label 时统计口径收敛到该版本分区；不传时按项目累计（前端
        "总计"展示口径）。

        返回: {"total": N, "total_chars": N, "total_bytes": N,
               "has_embeddings": bool, "vector_count": N,
               "current_version_label": "v1.0", "current_version_chunks": N,
               "types": {"api": {"count": 5, "chars": 1234, "bytes": 5678, "vectors": 5}, ...}}
        用于 RAG 面板分类明细展示。
        """
        project = project or self._get_project_name()
        scope_type, scope_id = self._project_scope(project)
        if not project:
            return {
                "total": 0,
                "total_chars": 0,
                "total_bytes": 0,
                "has_embeddings": False,
                "vector_count": 0,
                "current_version_label": "",
                "current_version_chunks": 0,
                "types": {},
            }
        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            # 版本分区统计：传版本时只看该版本，不传时按项目累计（前端"总计"）
            _ver_sql = " AND version_label=%s" if version_label else ""
            _ver_args = (version_label,) if version_label else ()
            rows = conn.execute(
                "SELECT type, COUNT(*) AS cnt, "
                "COALESCE(SUM(CHAR_LENGTH(text)), 0) AS chars, "
                "COALESCE(SUM(LENGTH(text)), 0) AS bytes, "
                "COALESCE(SUM(vector_blob IS NOT NULL), 0) AS vectors "
                "FROM rag_chunks "
                "WHERE scope_type=%s AND scope_id=%s AND is_active=1" + _ver_sql +
                " GROUP BY type ORDER BY cnt DESC",
                (scope_type, scope_id, *_ver_args),
            ).fetchall()
            types = {}
            total = 0
            total_chars = 0
            total_bytes = 0
            total_vectors = 0
            for r in rows or []:
                t = r["type"]
                c = r["cnt"]
                ch = r["chars"]
                by = r["bytes"]
                v = r["vectors"]
                types[t] = {"count": c, "chars": ch, "bytes": by, "vectors": v}
                total += c
                total_chars += ch
                total_bytes += by
                total_vectors += v
            # 上传文档按 6 类子类型展示（不复用笼统的 'upload'，否则与真实类型重复计数）；
            # 类型集合与分类映射统一取自 upload_documents 单一事实源。
            from src.core.upload_documents import (
                UPLOAD_CATEGORY_CHUNK_TYPES,
                fetch_uploaded_documents,
            )

            _ALL_TYPES = [
                "api",
                "db_table",
                "figma_page",
                "figma_node",
                "figma_component",
                "requirement",
                *UPLOAD_CATEGORY_CHUNK_TYPES.values(),
            ]
            for t in _ALL_TYPES:
                types.setdefault(t, {"count": 0, "chars": 0, "bytes": 0, "vectors": 0})
            # 分类权重来自同一读取入口（按当前版本取平均），不再自拼 SQL 推导
            _w_label = version_label
            if not _w_label:
                from src.users.project_auth import get_active_version_label

                _w_label = get_active_version_label(project)
            _weights = {}
            for _doc in fetch_uploaded_documents(project, _w_label):
                if _doc.get("weight") is not None:
                    _weights.setdefault(_doc.get("category"), []).append(
                        float(_doc["weight"])
                    )
            for _cat, _ws in _weights.items():
                _tk = UPLOAD_CATEGORY_CHUNK_TYPES.get(_cat)
                if _tk and _tk in types:
                    types[_tk]["weight"] = round(sum(_ws) / len(_ws), 2)
            # ★ v6: 统计当前版本的 chunks 数量
            # ★ 修复(2026-08-14): 统一使用 state._current_version() 与 _save_to_db 同源，
            #   避免 UI 选择版本与 DB active 版本不一致导致 current_version_chunks 恒为 0。
            # 已按版本统计时（version_label 入参），当前版本数量就是 total，无需二次查询。
            current_version_label = version_label
            current_version_chunks = total if version_label else 0
            if not version_label:
                with guard("_get_chunk_stats_by_type: from src.backend.state import _curre", log=_log):
                    # 优先使用 state thread-local（与 _save_to_db 同源）
                    from src.backend.state import _current_version

                    current_version_label = _current_version()
                if not current_version_label:
                    try:
                        from src.core.versioning import current_version_label as _get_ver_label

                        current_version_label = _get_ver_label(project)
                    except Exception as _ve:
                        _log.debug(
                            "[RagEngine] 当前版本 chunks 统计失败: %s", _ve, exc_info=True
                        )
                if current_version_label:
                    try:
                        cv_row = conn.execute(
                            "SELECT COUNT(*) AS cnt FROM rag_chunks "
                            "WHERE scope_type=%s AND scope_id=%s AND is_active=1 AND version_label=%s",
                            (scope_type, scope_id, current_version_label),
                        ).fetchone()
                        current_version_chunks = int(cv_row["cnt"] if cv_row else 0)
                    except Exception as _ve:
                        _log.debug(
                            "[RagEngine] 当前版本 chunks 统计失败: %s", _ve, exc_info=True
                        )
            return {
                "total": total,
                "total_chars": total_chars,
                "total_bytes": total_bytes,
                "has_embeddings": total_vectors > 0,
                "vector_count": total_vectors,
                "current_version_label": current_version_label,
                "current_version_chunks": current_version_chunks,
                "types": types,
            }
        except Exception as e:
            _log.warning("[RagEngine] 按类型统计 chunk 失败: %s", e)
            return {
                "total": 0,
                "total_chars": 0,
                "total_bytes": 0,
                "has_embeddings": False,
                "vector_count": 0,
                "current_version_label": "",
                "current_version_chunks": 0,
                "types": {},
            }
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    def clear_index(self, user_id: str = "", version_label: str = "") -> None:
        """清空「项目 + 版本」分区的索引（删除 MySQL rag_chunks + 内存状态）。

        Args:
            user_id: 用户ID。非空时仅清除该用户的 chunks；空时清除该分区所有 chunks。
            version_label: 版本标签；为空时取当前上下文版本。分区语义下不允许跨版本清空。
        """
        self._chunks = []
        self._bm25_index = {}
        self._use_embeddings = False
        self._detected_metric = None

        project = self._get_project_name()
        version_label = version_label or self._get_project_version()
        scope_type, scope_id = self._project_scope(project)
        if not project or not version_label:
            _log.warning(
                "[RagEngine] 项目(%s)或版本(%s)上下文缺失，跳过清空 MySQL 索引",
                project or "?",
                version_label or "?",
            )
            _log.info("[RagEngine] 内存索引已清空")
            return
        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            if user_id:
                conn.execute(
                    "DELETE FROM rag_chunks WHERE scope_type = %s AND scope_id = %s "
                    "AND version_label = %s AND user_id = %s",
                    (scope_type, scope_id, version_label, user_id),
                )
                _log.info(
                    "[RagEngine] MySQL 索引已清空 (project=%s, version=%s, user=%s)",
                    project,
                    version_label,
                    user_id,
                )
            else:
                conn.execute(
                    "DELETE FROM rag_chunks WHERE scope_type = %s AND scope_id = %s "
                    "AND version_label = %s",
                    (scope_type, scope_id, version_label),
                )
                _log.info(
                    "[RagEngine] MySQL 索引已清空 (project=%s, version=%s)",
                    project,
                    version_label,
                )
            conn.commit()
        except Exception as e:
            _log.warning("[RagEngine] 清空 MySQL 索引失败: %s", e)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)
        _log.info("[RagEngine] 内存索引已清空")
