# -*- coding: utf-8 -*-
"""★ P2-11 MultimodalStore — 多模态 RAG 块存储（CRUD）。

定位：
  multimodal_chunks 表的读写层。将需求文档/DOM 快照/Figma 设计稿统一切块存储，
  供 retriever.py 联合检索。替代现有分散的 RAG 逻辑（rag_engine 仅做文本检索，
  缺少跨模态联合）。

职责边界（§12.4）：
  - 仅做 CRUD（增删改查），不做检索/分词/向量化
  - 不依赖业务术语，source_type 用通用枚举（requirement/dom/figma）
  - 降级：写入失败不阻断主流程，返回 0/False

通用性（§12.4）：
  1. 跨系统：source_type 枚举覆盖所有数据源类型
  2. 跨行业：content 是通用文本，不绑定业务
  3. 跨页面：dom 类型的 ref_id 是 page_url，通用
  4. 降级：异常返回空结果，不阻断
  5. 职责边界：仅 CRUD，检索由 retriever.py 负责
"""
from __future__ import annotations
from src.core.guarded import guard

import json
import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)

_TABLE = "multimodal_chunks"

# source_type 枚举（通用，不含业务术语）
SOURCE_TYPES = frozenset({"requirement", "dom", "figma"})


def _active_version(project: str = "") -> int:
    """当前项目 active version_id（0=legacy）。"""
    from src.core.versioning import current_version_id

    return int(current_version_id(project) or 0)


def _ensure_multimodal_table(conn):
    """确保 multimodal_chunks 存在 version_id 列（存量库运行时 ALTER 兜底）。"""
    with guard("_ensure_multimodal_table: _cols = { r['COLUMN_NAME'] for r in ", log=_log, level=logging.WARNING):
        _cols = {
            r["COLUMN_NAME"]
            for r in conn.execute(
                "SELECT COLUMN_NAME FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'multimodal_chunks'"
            ).fetchall()
        }
        if "version_id" not in _cols:
            conn.execute(
                "ALTER TABLE multimodal_chunks ADD COLUMN version_id BIGINT NOT NULL DEFAULT 0"
            )
            conn.commit()


class MultimodalStore:
    """multimodal_chunks 表的 CRUD 操作层。

    使用方式:
        store = MultimodalStore()
        # 添加块
        chunk_id = store.add_chunk("project", "requirement", "req-001", "需求文本...")
        # 查询块
        chunks = store.get_chunks("project", source_type="requirement")
        # 删除块
        deleted = store.delete_chunks("project", source_type="dom", ref_id="/old_page")
    """

    def __init__(self):
        self._last_error: str = ""

    def _get_conn(self):
        """获取数据库连接。"""
        from src.models.base import get_conn

        return get_conn()

    # ═══════════════════════════════════════════════════════════════
    # Create
    # ═══════════════════════════════════════════════════════════════

    def add_chunk(
        self,
        project: str,
        source_type: str,
        ref_id: str,
        content: str,
        embedding: Optional[list[float]] = None,
        version_id: Optional[int] = None,
    ) -> int:
        """添加一个 RAG 块。

        参数:
            project: 项目名
            source_type: 来源类型（requirement/dom/figma）
            ref_id: 来源对象 ID（如 req-001、page_url、figma_node_id）
            content: 块文本内容
            embedding: 向量（可选，JSON 数组）

        返回: 新增记录 ID（0=失败）
        """
        if not project or not source_type or not content:
            self._last_error = "missing project/source_type/content"
            return 0
        emb_json = json.dumps(embedding, ensure_ascii=False) if embedding else None
        conn = None
        try:
            conn = self._get_conn()
            _ensure_multimodal_table(conn)
            active = (
                int(version_id or 0)
                if version_id is not None
                else _active_version(project)
            )
            cursor = conn.execute(
                "INSERT INTO %s (project, version_id, source_type, ref_id, content, embedding, created_at) "
                "VALUES (%%s, %%s, %%s, %%s, %%s, %%s, NOW())" % _TABLE,
                (
                    project,
                    active,
                    source_type,
                    str(ref_id or "")[:128],
                    content,
                    emb_json,
                ),
            )
            conn.commit()
            self._last_error = ""
            return int(cursor.lastrowid or 0)
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[MultimodalStore] add_chunk 失败: %s", str(e)[:120])
            return 0
        finally:
            if conn:
                with guard("add_chunk: conn.close()", log=_log):
                    conn.close()

    def add_chunks_batch(
        self,
        project: str,
        source_type: str,
        chunks: list[dict[str, Any]],
    ) -> int:
        """批量添加 RAG 块。

        参数:
            chunks: [{ref_id, content, embedding?}, ...]

        返回: 成功添加的数量
        """
        if not project or not source_type or not chunks:
            return 0
        conn = None
        count = 0
        try:
            conn = self._get_conn()
            _ensure_multimodal_table(conn)
            active = _active_version(project)
            for ch in chunks:
                ref_id = str(ch.get("ref_id") or "")[:128]
                content = ch.get("content") or ""
                if not content:
                    continue
                emb = ch.get("embedding")
                emb_json = json.dumps(emb, ensure_ascii=False) if emb else None
                conn.execute(
                    "INSERT INTO %s (project, version_id, source_type, ref_id, content, embedding, created_at) "
                    "VALUES (%%s, %%s, %%s, %%s, %%s, %%s, NOW())" % _TABLE,
                    (project, active, source_type, ref_id, content, emb_json),
                )
                count += 1
            conn.commit()
            self._last_error = ""
            return count
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[MultimodalStore] add_chunks_batch 失败: %s", str(e)[:120])
            with guard("add_chunks_batch: if conn: conn.rollback()", log=_log):
                if conn:
                    conn.rollback()
            return count
        finally:
            if conn:
                with guard("add_chunks_batch: conn.close()", log=_log):
                    conn.close()

    # ═══════════════════════════════════════════════════════════════
    # Read
    # ═══════════════════════════════════════════════════════════════

    def get_chunks(
        self,
        project: str,
        source_type: Optional[str] = None,
        ref_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """查询 RAG 块。

        参数:
            project: 项目名
            source_type: 来源类型（可选，None=所有类型）
            ref_id: 来源对象 ID（可选，None=所有）
            limit: 最多返回数量

        返回: [{id, project, source_type, ref_id, content, embedding, created_at}, ...]
        """
        if not project:
            return []
        conn = None
        try:
            conn = self._get_conn()
            _ensure_multimodal_table(conn)
            active = _active_version(project)
            sql = (
                "SELECT id, project, source_type, ref_id, content, embedding, created_at "
                "FROM %s WHERE project=%%s AND version_id=%%s" % _TABLE
            )
            params: list[Any] = [project, active]
            if source_type:
                sql += " AND source_type=%s"
                params.append(source_type)
            if ref_id:
                sql += " AND ref_id=%s"
                params.append(ref_id)
            sql += " ORDER BY created_at ASC LIMIT %s"
            params.append(int(limit))
            rows = conn.execute(sql, tuple(params)).fetchall()
            self._last_error = ""
            result = []
            for r in rows:
                emb = r.get("embedding")
                if isinstance(emb, str):
                    try:
                        emb = json.loads(emb)
                    except (json.JSONDecodeError, ValueError):
                        emb = None
                result.append(
                    {
                        "id": int(r.get("id") or 0),
                        "project": r.get("project") or "",
                        "source_type": r.get("source_type") or "",
                        "ref_id": r.get("ref_id") or "",
                        "content": r.get("content") or "",
                        "embedding": emb,
                        "created_at": str(r.get("created_at") or ""),
                    }
                )
            return result
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[MultimodalStore] get_chunks 失败: %s", str(e)[:120])
            return []
        finally:
            if conn:
                with guard("get_chunks: conn.close()", log=_log):
                    conn.close()

    def get_chunk_by_id(self, chunk_id: int) -> Optional[dict[str, Any]]:
        """按 ID 查询单个块。"""
        if not chunk_id:
            return None
        conn = None
        try:
            conn = self._get_conn()
            _ensure_multimodal_table(conn)
            row = conn.execute(
                "SELECT id, project, source_type, ref_id, content, embedding, created_at "
                "FROM %s WHERE id=%%s AND version_id=%%s" % _TABLE,
                (int(chunk_id), _active_version()),
            ).fetchone()
            self._last_error = ""
            if not row:
                return None
            emb = row.get("embedding")
            if isinstance(emb, str):
                try:
                    emb = json.loads(emb)
                except (json.JSONDecodeError, ValueError):
                    emb = None
            return {
                "id": int(row.get("id") or 0),
                "project": row.get("project") or "",
                "source_type": row.get("source_type") or "",
                "ref_id": row.get("ref_id") or "",
                "content": row.get("content") or "",
                "embedding": emb,
                "created_at": str(row.get("created_at") or ""),
            }
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[MultimodalStore] get_chunk_by_id 失败: %s", str(e)[:120])
            return None
        finally:
            if conn:
                with guard("get_chunk_by_id: conn.close()", log=_log):
                    conn.close()

    # ═══════════════════════════════════════════════════════════════
    # Delete
    # ═══════════════════════════════════════════════════════════════

    def delete_chunks(
        self,
        project: str,
        source_type: Optional[str] = None,
        ref_id: Optional[str] = None,
    ) -> int:
        """删除 RAG 块。

        参数:
            project: 项目名
            source_type: 来源类型（可选，None=所有类型）
            ref_id: 来源对象 ID（可选，None=所有）

        返回: 删除的行数
        """
        if not project:
            return 0
        conn = None
        try:
            conn = self._get_conn()
            _ensure_multimodal_table(conn)
            active = _active_version(project)
            sql = "DELETE FROM %s WHERE project=%%s AND version_id=%%s" % _TABLE
            params: list[Any] = [project, active]
            if source_type:
                sql += " AND source_type=%s"
                params.append(source_type)
            if ref_id:
                sql += " AND ref_id=%s"
                params.append(ref_id)
            cursor = conn.execute(sql, tuple(params))
            conn.commit()
            self._last_error = ""
            return int(cursor.rowcount or 0)
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[MultimodalStore] delete_chunks 失败: %s", str(e)[:120])
            with guard("delete_chunks: if conn: conn.rollback()", log=_log):
                if conn:
                    conn.rollback()
            return 0
        finally:
            if conn:
                with guard("delete_chunks: conn.close()", log=_log):
                    conn.close()

    # ═══════════════════════════════════════════════════════════════
    # Stats
    # ═══════════════════════════════════════════════════════════════

    def count_chunks(
        self,
        project: str,
        source_type: Optional[str] = None,
    ) -> int:
        """统计 RAG 块数量。

        返回: 块数量（0=无或异常）
        """
        if not project:
            return 0
        conn = None
        try:
            conn = self._get_conn()
            _ensure_multimodal_table(conn)
            active = _active_version(project)
            sql = (
                "SELECT COUNT(*) AS cnt FROM %s WHERE project=%%s AND version_id=%%s"
                % _TABLE
            )
            params: list[Any] = [project, active]
            if source_type:
                sql += " AND source_type=%s"
                params.append(source_type)
            row = conn.execute(sql, tuple(params)).fetchone()
            self._last_error = ""
            return int(row.get("cnt") or 0) if row else 0
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[MultimodalStore] count_chunks 失败: %s", str(e)[:120])
            return 0
        finally:
            if conn:
                with guard("count_chunks: conn.close()", log=_log):
                    conn.close()

    def get_stats(self, project: str) -> dict[str, int]:
        """获取按 source_type 分组的统计。

        返回: {requirement: N, dom: N, figma: N, total: N}
        """
        if not project:
            return {}
        conn = None
        try:
            conn = self._get_conn()
            _ensure_multimodal_table(conn)
            active = _active_version(project)
            rows = conn.execute(
                "SELECT source_type, COUNT(*) AS cnt "
                "FROM %s WHERE project=%%s AND version_id=%%s GROUP BY source_type" % _TABLE,
                (project, active),
            ).fetchall()
            self._last_error = ""
            stats: dict[str, int] = {"requirement": 0, "dom": 0, "figma": 0, "total": 0}
            total = 0
            for r in rows:
                st = r.get("source_type") or ""
                cnt = int(r.get("cnt") or 0)
                if st in stats:
                    stats[st] = cnt
                total += cnt
            stats["total"] = total
            return stats
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[MultimodalStore] get_stats 失败: %s", str(e)[:120])
            return {"requirement": 0, "dom": 0, "figma": 0, "total": 0}
        finally:
            if conn:
                with guard("get_stats: conn.close()", log=_log):
                    conn.close()


# ═══════════════════════════════════════════════════════════════
# 单例（惰性初始化）
# ═══════════════════════════════════════════════════════════════

_default_store: Optional[MultimodalStore] = None


def get_store() -> MultimodalStore:
    """获取默认 MultimodalStore 实例（单例，惰性初始化）。"""
    global _default_store
    if _default_store is None:
        _default_store = MultimodalStore()
    return _default_store
