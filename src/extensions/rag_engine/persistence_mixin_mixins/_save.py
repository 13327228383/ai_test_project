# -*- coding: utf-8 -*-
"""
保存 mixin：MySQL 持久化（rag_chunks 表）、ChromaDB 交互
"""

import json
from src.core.guarded import guard
import logging

_log = logging.getLogger("rag_engine")

# ★ 性能优化：rag_chunks 的 DDL 只跑一次（首个连接触发），后续请求直接跳过
_rag_columns_ensured = False


class PersistenceSaveMixin:
    """MySQL 持久化相关方法。"""

    # ═══════════════════════════════════════════
    # 项目信息辅助
    # ═══════════════════════════════════════════

    @staticmethod
    def _get_project_name() -> str:
        """获取当前项目名（统一来源，消除双重上下文不一致）。

        优先级：state._current_project()（与 dashboard 同源）→
                config.project_name（ConfigRegistry 回退）→
                空字符串（拒绝静默写入 "default_project"）。
        ★ 修复(2026-08-14): 原使用 config.project_name + 硬编码 "default_project" 兜底，
          导致项目上下文丢失时数据静默写入错误 scope，dashboard 查不到。
        """
        with guard("_get_project_name: from src.backend.state import _curre", log=_log):
            # 优先使用 state 的 thread-local（与 dashboard / API 守卫同源）
            from src.backend.state import _current_project

            proj = _current_project()
            if proj:
                return proj

        try:
            from src.core.config_loader import config as _cfg

            proj = getattr(_cfg, "project_name", "") or str(getattr(_cfg, "project", ""))
            if proj:
                return proj
        except Exception as e:
            _log.warning("[RagEngine] 项目名获取异常: %s", e, exc_info=True)

        # 无项目上下文 → 返回空，由调用方 _save_to_db / _load_chunks 跳过操作并告警
        _log.warning("[RagEngine] 无法获取项目名（_current_project 和 config 均为空），RAG 操作将跳过")
        return ""

    @staticmethod
    def _get_project_version() -> str:
        """获取当前版本标签（统一来源，消除双重上下文不一致）。

        优先级：state._current_version()（与 UI 选择同源）→
                config.project_version（DB active 版本回退）→
                空字符串。
        ★ 修复(2026-08-14): 原仅使用 config.project_version（永远返回 DB active 版本），
          与用户在 UI 中选择的版本可能不同。现优先使用 state._current_version()。
        """
        with guard("_get_project_version: from src.backend.state import _curre", log=_log):
            from src.backend.state import _current_version

            ver = _current_version()
            if ver:
                return ver

        try:
            from src.core.config_loader import config as _cfg

            return str(getattr(_cfg, "project_version", "") or "").strip()
        except Exception as e:
            _log.warning("[RagEngine] 版本号获取异常: %s", e, exc_info=True)
            return ""

    @staticmethod
    def _project_scope(project: str):
        project = str(project or "").strip()
        return ("project", project)

    @staticmethod
    def _ensure_project(project: str) -> None:
        """确保项目在 projects 表中存在（用于 rag_chunks 的外键约束）。
        ★ 2026-06-22：建表已统一由 src/core/schema.sql 负责。
        """
        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            # 插入项目（如果不存在）
            conn.execute("INSERT IGNORE INTO projects (name) VALUES (%s)", (project,))
            conn.commit()
        except Exception:
            _log.debug(
                "[RagEngine] 项目 %s 写入 projects 表失败（已存在或表未就绪），不阻断索引构建",
                project,
            )
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    # ═══════════════════════════════════════════
    # MySQL 持久化（rag_chunks 表）
    # ═══════════════════════════════════════════

    def _save_to_db(
        self, user_id: str = "", project: str = "", version_label: str = ""
    ) -> None:
        """保存索引到 MySQL rag_chunks 表（按「项目 + 版本」分区 upsert）。

        - ★ v3: executemany 批量 INSERT ON DUPLICATE KEY UPDATE（chunk_id 稳定键）
        - 主键含 version_label：同一 chunk_id 在不同版本各占一行，互不覆盖
        - is_active=1 + source_version_label 记录最后构建版本
        - 构建完当前类型后：同版本同 type 不在本次集合的 chunk 批量设 is_active=0
        - ★ B3: user_id 用户级隔离，非空时仅操作该用户的 chunks
        - project/version_label 由 build_index 入口线程显式传入时以其为准，
          避免在保存路径二次推导项目/版本上下文。
        """
        project = project or self._get_project_name()
        version_label = version_label or self._get_project_version()
        scope_type, scope_id = self._project_scope(project)
        if not project or not version_label:
            _log.warning(
                "[RagEngine] 项目(%s)或版本(%s)上下文缺失，跳过保存到 MySQL",
                project or "?",
                version_label or "?",
            )
            return
        uid = user_id or ""
        conn = None
        try:
            from src.core.db_manager import get_connection

            conn = get_connection()
            raw = conn.cursor()

            self._ensure_rag_columns(conn)

            try:
                from src.core.config_loader import config as _cfg

                embed_model = str(getattr(_cfg, "ollama_embed_model", "") or "")
            except Exception:
                embed_model = ""

            current_chunk_ids_by_type: dict = {}
            rows = []
            for c in self._chunks:
                ctype = str(c.get("type", "") or "")
                cid = str(c.get("chunk_id", "") or "")
                current_chunk_ids_by_type.setdefault(ctype, set()).add(cid)

                vec = c.get("_vector")
                vec_blob = self._vector_to_blob(vec) if vec else None
                vec_json_str = json.dumps(vec, ensure_ascii=False) if vec else None
                vec_norm = self._l2_norm(vec) if vec else None
                vec_dim = len(vec) if vec else 0
                slice_id = str(c.get("slice_id") or "").strip()
                chapter_hash = str(c.get("chapter_hash") or "").strip()
                rows.append(
                    (
                        scope_type,
                        scope_id,
                        uid,
                        version_label,
                        cid,
                        ctype,
                        slice_id,
                        c["text"],
                        json.dumps(
                            c.get("meta") if c.get("meta") else {}, ensure_ascii=False
                        ),
                        json.dumps(c.get("keywords", []), ensure_ascii=False),
                        vec_blob,
                        vec_json_str,
                        vec_norm,
                        vec_dim,
                        embed_model,
                        chapter_hash,
                        version_label,
                    )
                )

            _BATCH_SIZE = 200
            _INSERT_SQL = (
                "INSERT INTO rag_chunks (scope_type, scope_id, user_id, version_label, chunk_id, type, slice_id, text, meta, "
                "keywords, vector_blob, vector_json, vector_norm, embed_dim, embed_model, chapter_hash, is_active, source_version_label) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s) "
                "ON DUPLICATE KEY UPDATE "
                "user_id=VALUES(user_id), version_label=VALUES(version_label), slice_id=VALUES(slice_id), text=VALUES(text), "
                "meta=VALUES(meta), keywords=VALUES(keywords), "
                "vector_blob=VALUES(vector_blob), vector_json=VALUES(vector_json), "
                "vector_norm=VALUES(vector_norm), embed_dim=VALUES(embed_dim), "
                "embed_model=VALUES(embed_model), chapter_hash=VALUES(chapter_hash), "
                "is_active=1, source_version_label=VALUES(source_version_label), "
                "updated_at=NOW()"
            )
            for i in range(0, len(rows), _BATCH_SIZE):
                batch = rows[i : i + _BATCH_SIZE]
                raw.executemany(_INSERT_SQL, batch)
            raw.close()

            for ctype, current_ids in current_chunk_ids_by_type.items():
                if not current_ids or ctype == "requirement":
                    continue
                if uid:
                    existing_rows = conn.execute(
                        "SELECT chunk_id FROM rag_chunks "
                        "WHERE scope_type=%s AND scope_id=%s AND version_label=%s "
                        "AND user_id=%s AND type=%s AND is_active=1",
                        (scope_type, scope_id, version_label, uid, ctype),
                    ).fetchall()
                else:
                    existing_rows = conn.execute(
                        "SELECT chunk_id FROM rag_chunks "
                        "WHERE scope_type=%s AND scope_id=%s AND version_label=%s "
                        "AND type=%s AND is_active=1",
                        (scope_type, scope_id, version_label, ctype),
                    ).fetchall()
                stale_ids = [
                    r["chunk_id"]
                    for r in (existing_rows or [])
                    if r["chunk_id"] not in current_ids
                ]
                if stale_ids:
                    placeholders = ",".join(["%s"] * len(stale_ids))
                    if uid:
                        conn.execute(
                            f"UPDATE rag_chunks SET is_active=0, deactivated_version_label=%s, updated_at=NOW() "
                            f"WHERE scope_type=%s AND scope_id=%s AND version_label=%s "
                            f"AND user_id=%s AND type=%s AND chunk_id IN ({placeholders})",
                            (
                                version_label,
                                scope_type,
                                scope_id,
                                version_label,
                                uid,
                                ctype,
                                *stale_ids,
                            ),
                        )
                    else:
                        conn.execute(
                            f"UPDATE rag_chunks SET is_active=0, deactivated_version_label=%s, updated_at=NOW() "
                            f"WHERE scope_type=%s AND scope_id=%s AND version_label=%s "
                            f"AND type=%s AND chunk_id IN ({placeholders})",
                            (
                                version_label,
                                scope_type,
                                scope_id,
                                version_label,
                                ctype,
                                *stale_ids,
                            ),
                        )
                    _log.info(
                        "[RagEngine] 已失效 %d 个 %s chunk (版本=%s)",
                        len(stale_ids),
                        ctype,
                        version_label,
                    )

            conn.commit()
            _log.info(
                "[RagEngine] 已保存到 MySQL rag_chunks: %d chunks (project=%s 版本=%s 用户=%s, 批量%d批)",
                len(self._chunks),
                project,
                version_label,
                uid or "共享",
                (len(rows) + _BATCH_SIZE - 1) // _BATCH_SIZE if rows else 0,
            )
        except Exception as e:
            _log.warning("[RagEngine] MySQL 保存失败: %s", e)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("非关键异常: %s", _pass_e, exc_info=True)

    @staticmethod
    def _ensure_rag_columns(conn) -> None:
        """确保 rag_chunks 表结构满足当前语义（DDL 迁移，每进程只跑一次）。

        - embed_model 列：记录生成向量的模型，用于一致性检测。
        - chapter_hash 列：段落级哈希，增量重建时比对（G1）。
        - 主键含 version_label：rag_chunks 按「项目 + 版本」分区，稳定键必须带版本，
          否则同一 chunk_id 的跨版本写入会互相覆盖（旧版本被改标成新版本）。
        """
        global _rag_columns_ensured
        if _rag_columns_ensured:
            return
        try:
            conn.execute(
                "ALTER TABLE rag_chunks ADD COLUMN embed_model VARCHAR(128) NOT NULL DEFAULT '' "
                "COMMENT '生成向量的嵌入模型名称（如 nomic-embed-text）' AFTER embed_dim"
            )
        except Exception:
            _log.debug(
                "[RagEngine] rag_chunks.embed_model 列 ALTER 失败（列已存在或表未就绪），跳过 DDL"
            )
        try:
            conn.execute(
                "ALTER TABLE rag_chunks ADD COLUMN chapter_hash VARCHAR(32) NOT NULL DEFAULT '' "
                "COMMENT '段落内容哈希（MD5前16位，增量重建比对）' AFTER embed_model"
            )
        except Exception:
            _log.debug(
                "[RagEngine] rag_chunks.chapter_hash 列 ALTER 失败（列已存在或表未就绪），跳过 DDL"
            )
        try:
            _pk_rows = (
                conn.execute(
                    "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'rag_chunks' "
                    "AND INDEX_NAME = 'PRIMARY' ORDER BY SEQ_IN_INDEX"
                ).fetchall()
                or []
            )
            _pk_cols = [r.get("COLUMN_NAME") for r in _pk_rows]
            if _pk_cols and "version_label" not in _pk_cols:
                conn.execute(
                    "ALTER TABLE rag_chunks DROP PRIMARY KEY, "
                    "ADD PRIMARY KEY (scope_type, scope_id, version_label, chunk_id)"
                )
                _log.info(
                    "[RagEngine] rag_chunks 主键扩展为 (scope_type, scope_id, version_label, chunk_id)"
                )
        except Exception as _pk_e:
            _log.warning(
                "[RagEngine] rag_chunks 主键迁移失败（版本分区未生效）: %s", _pk_e
            )
        _rag_columns_ensured = True
