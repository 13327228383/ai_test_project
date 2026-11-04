# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib

"""
需求切片 MySQL 存储（07-05h 新增）
==================================

持久化 Step2 _smart_chunk_requirement 的切片结果到 requirement_slices 表。
支持关系链路查看：从需求/用例/文档任意节点展开到切片级别。

设计：
  - 切片在 Step2 处理时持久化（_smart_chunk_requirement 后调用 save_slices）
  - 切片ID稳定（req_raw:0 / req_raw:pre:1 / req_raw:1:2），可按 slice_id 精确查询
  - 切片带 feature_label（Phase4 聚类结果），可按功能筛选
  - 切片带 needs_review 标记（相似度合并触发），可筛选需审核的切片

核心方法：
  - save_slices(slices) — 批量保存切片（REPLACE INTO）
  - list_slices() — 列出当前项目当前版本所有切片
  - get_slice(slice_id) — 按 slice_id 查询单条切片
  - get_slices_by_feature(feature_label) — 按功能标签查询切片
  - clear_project_slices() — 清空当前项目当前版本切片
  - count() — 切片总数
"""

import json
import logging
import threading
from typing import Any

_log = logging.getLogger(__name__)


# ★ DDL 只跑一次（首个连接触发），后续请求直接跳过
_slice_tables_ensured = False


class RequirementSliceStore:
    """需求切片 MySQL 读写工具（单例）。"""

    def __init__(self, db_getter=None):
        self._last_error = ""
        self._db_getter = db_getter
        self._lock = threading.Lock()

    def _get_db(self):
        if self._db_getter is not None:
            return self._db_getter()
        from src.core.db_manager import get_connection

        return get_connection()

    @staticmethod
    def _ensure_tables(c) -> None:
        """占位函数（requirement_slices 表已统一由 src/core/schema.sql 负责）。"""
        global _slice_tables_ensured
        if _slice_tables_ensured:
            return
        _slice_tables_ensured = True
        return

    @staticmethod
    def _to_json(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception as e:
            _log.warning("[SliceStore] _to_json 序列化失败: %s", e)
            return "[]"

    def _get_project_version(self) -> tuple:
        """获取当前项目名与版本三元组（project, version_id, version_label）。

        版本解析统一走 src.core.versioning（execution_version_context → active version），
        与 requirements / trace_links 落库同一权威源；否则切片会落到 version_id=0，
        与真实 active 版本分区错位，按版本清理/查询都命中不到。
        """
        try:
            from src.core.config_loader import config
            from src.core.versioning import current_version_id, current_version_label

            project = getattr(config, "project_name", "") or ""
            if not project:
                return "", 0, ""
            return (
                project,
                current_version_id(project),
                current_version_label(project),
            )
        except Exception as e:
            _log.warning("[SliceStore] 读取项目/版本上下文失败: %s", e)
            return "", 0, ""

    def save_slices(
        self,
        slices: list[dict[str, Any]],
        project: str | None = None,
        version_label: str | None = None,
    ) -> int:
        """批量保存切片（事务化：先 DELETE 清同项目同版本旧行 → 再 REPLACE INTO）。

        DELETE + REPLACE 必须在同一事务：调用方若省略 clear 或多轮触发 Step2，
        非原子写法会残留旧行；表上的 UNIQUE(project, version_id, text_hash)
        是第二道防线，同文本不同 slice_id 的重复行由存储引擎挡住。

        text_hash 统一在本方法内计算 md5(text.strip())[:16] 写入 UK 列，
        所有调用方共用同一实现（单一权威源）。

        Args:
            slices: 切片列表，每个切片含：
                - slice_id: 切片ID（必需）
                - text: 切片原文（必需）
                - section: 章节标题（可选，默认空）
                - slice_index: 序号（可选，默认0）
                - needs_review: 是否需审核（可选，默认0）
                - merge_reasons: 合并原因列表（可选）
                - feature_label: 功能标签（可选，默认空）
                - doc_type: 文档类型（可选，默认requirement）
                - doc_anchor: 文档锚点（可选，默认空）
            project: 项目名；未传时取当前项目上下文
            version_label: 版本标签；未传时取当前 active 版本标签

        Returns:
            成功保存的切片数，失败返回 -1
        """
        if not slices:
            return 0

        _project, version_id, _version_label = self._get_project_version()
        project = project or _project
        version_label = version_label or _version_label
        if not project:
            _log.warning("[SliceStore] save_slices: 未设置当前项目")
            return -1

        conn = None
        try:
            conn = self._get_db()
            self._ensure_tables(conn)

            _HARD_DELETE_SQL = (
                "DELETE FROM requirement_slices WHERE project = %s AND version_id = %s"
            )
            _REPLACE_SQL = (
                "REPLACE INTO requirement_slices "
                "(slice_id, project, version_id, version_label, doc_type, doc_anchor, "
                " section, slice_index, text, text_hash, char_count, needs_review, "
                " merge_reasons_json, feature_label) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            )

            rows = []
            for s in slices:
                slice_id = str(s.get("slice_id") or s.get("chunk_id") or "").strip()
                if not slice_id:
                    continue
                text = str(s.get("text") or "")
                # ★ SSOT text_hash 计算：一个地方算，所有调用方受益
                text_hash = hashlib.md5(text.strip().encode()).hexdigest()[:16] if text else ""
                rows.append(
                    (
                        slice_id,
                        project,
                        version_id,
                        version_label,
                        str(s.get("doc_type") or "requirement"),
                        str(s.get("doc_anchor") or ""),
                        str(s.get("section") or ""),
                        int(s.get("slice_index") or s.get("index") or 0),
                        text,
                        text_hash,
                        len(text),
                        1 if s.get("needs_review") else 0,
                        self._to_json(s.get("merge_reasons") or []),
                        str(s.get("feature_label") or ""),
                    )
                )

            if not rows:
                return 0

            with self._lock:
                cur = conn.cursor()
                # ★ 事务原子：DELETE 清旧行 + REPLACE INTO 新行，同事务
                cur.execute(_HARD_DELETE_SQL, (project, version_id))
                _log.info("[SliceStore] 清旧切片 %d 条 (project=%s, version_id=%s)", cur.rowcount, project, version_id)
                cur.executemany(_REPLACE_SQL, rows)
                conn.commit()
                _log.info(
                    "[SliceStore] 保存 %d 条切片 (project=%s, version_id=%s)",
                    len(rows),
                    project,
                    version_id,
                )
                return len(rows)
        except Exception as e:
            self._last_error = str(e)
            _log.error("[SliceStore] save_slices 失败: %s", e)
            try:
                if conn:
                    conn.rollback()
            except Exception as _e:
                _log.debug("conn.rollback 失败: %s", _e, exc_info=True)
            return -1
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)

    def list_slices(
        self, project: str | None = None, version_id: int | None = None
    ) -> list[dict[str, Any]]:
        """列出当前项目当前版本所有切片（按 slice_index 排序）。

        Returns:
            切片字典列表，每个含所有字段
        """
        conn = None
        try:
            if project is None or version_id is None:
                project, version_id, _ = self._get_project_version()
            if not project:
                return []

            conn = self._get_db()
            self._ensure_tables(conn)

            sql = (
                "SELECT slice_id, project, version_id, version_label, doc_type, doc_anchor, "
                "       section, slice_index, text, char_count, needs_review, "
                "       merge_reasons_json, feature_label, created_at "
                "FROM requirement_slices "
                "WHERE project = %s AND version_id = %s "
                "ORDER BY slice_index"
            )
            cur = conn.execute(sql, (project, version_id))
            rows = cur.fetchall() or []

            result = []
            for r in rows:
                try:
                    merge_reasons = json.loads(r.get("merge_reasons_json") or "[]")
                except Exception:
                    merge_reasons = []
                result.append(
                    {
                        "slice_id": r.get("slice_id", ""),
                        "project": r.get("project", ""),
                        "version_id": r.get("version_id", 0),
                        "version_label": r.get("version_label", ""),
                        "doc_type": r.get("doc_type", "requirement"),
                        "doc_anchor": r.get("doc_anchor", ""),
                        "section": r.get("section", ""),
                        "slice_index": r.get("slice_index", 0),
                        "text": r.get("text", ""),
                        "char_count": r.get("char_count", 0),
                        "needs_review": bool(r.get("needs_review", 0)),
                        "merge_reasons": merge_reasons,
                        "feature_label": r.get("feature_label", ""),
                        "created_at": str(r.get("created_at", "")),
                    }
                )
            return result
        except Exception as e:
            self._last_error = str(e)
            _log.error("[SliceStore] list_slices 失败: %s", e)
            return []
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)

    def get_slice(self, slice_id: str) -> dict[str, Any] | None:
        """按 slice_id 查询单条切片。"""
        if not slice_id:
            return None
        slices = self.list_slices()
        for s in slices:
            if s.get("slice_id") == slice_id:
                return s
        return None

    def get_slice_text_map(
        self,
        slice_ids: list[str],
        project: str | None = None,
        version_id: int | None = None,
    ) -> dict[str, str]:
        """批量按 slice_id 查询切片原文，返回 {slice_id: text} 映射。

        用途：Step3 为一批需求统一加载原文上下文，避免每条需求单独查询。
        """
        if not slice_ids:
            return {}
        if project is None or version_id is None:
            project, version_id, _ = self._get_project_version()
        if not project:
            return {}

        # 去重并过滤空值
        ids = [str(s).strip() for s in slice_ids if s]
        if not ids:
            return {}

        conn = None
        try:
            conn = self._get_db()
            self._ensure_tables(conn)
            # 分批避免 IN 子句过长（MySQL max_allowed_packet 限制）
            text_map = {}
            batch_size = 500
            for i in range(0, len(ids), batch_size):
                batch = ids[i : i + batch_size]
                placeholders = ",".join(["%s"] * len(batch))
                sql = (
                    f"SELECT slice_id, text FROM requirement_slices "
                    f"WHERE project = %s AND version_id = %s AND slice_id IN ({placeholders})"
                )
                cur = conn.execute(sql, (project, version_id) + tuple(batch))
                for r in cur.fetchall() or []:
                    text_map[str(r.get("slice_id", ""))] = r.get("text", "") or ""
            return text_map
        except Exception as e:
            self._last_error = str(e)
            _log.error("[SliceStore] get_slice_text_map 失败: %s", e)
            return {}
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)

    def get_slices_by_feature(self, feature_label: str) -> list[dict[str, Any]]:
        """按功能标签查询切片。"""
        if not feature_label:
            return []
        slices = self.list_slices()
        return [s for s in slices if s.get("feature_label") == feature_label]

    def update_feature_labels(self, slice_features: dict[str, str]) -> int:
        """批量更新切片的功能标签。

        Args:
            slice_features: {slice_id: feature_label}

        Returns:
            成功更新的切片数
        """
        if not slice_features:
            return 0

        project, version_id, _ = self._get_project_version()
        if not project:
            return -1

        conn = None
        try:
            conn = self._get_db()
            sql = (
                "UPDATE requirement_slices SET feature_label = %s "
                "WHERE project = %s AND version_id = %s AND slice_id = %s"
            )
            rows = [
                (label, project, version_id, sid)
                for sid, label in slice_features.items()
                if sid and label
            ]
            if not rows:
                return 0

            with self._lock:
                cur = conn.cursor()
                cur.executemany(sql, rows)
                conn.commit()
                return len(rows)
        except Exception as e:
            self._last_error = str(e)
            _log.error("[SliceStore] update_feature_labels 失败: %s", e)
            try:
                if conn:
                    conn.rollback()
            except Exception as _e:
                _log.debug("conn.rollback 失败: %s", _e, exc_info=True)
            return -1
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)

    def clear_project_slices(
        self, project: str | None = None, version_id: int | None = None
    ) -> bool:
        """清空当前项目当前版本切片。"""
        if project is None or version_id is None:
            project, version_id, _ = self._get_project_version()
        if not project:
            return False

        conn = None
        try:
            conn = self._get_db()
            with self._lock:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM requirement_slices WHERE project = %s AND version_id = %s",
                    (project, version_id),
                )
                conn.commit()
                _log.info(
                    "[SliceStore] 清空切片 (project=%s, version_id=%s)",
                    project,
                    version_id,
                )
                return True
        except Exception as e:
            self._last_error = str(e)
            _log.error("[SliceStore] clear_project_slices 失败: %s", e)
            try:
                if conn:
                    conn.rollback()
            except Exception as _e:
                _log.debug("conn.rollback 失败: %s", _e, exc_info=True)
            return False
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)

    def count(self, project: str | None = None, version_id: int | None = None) -> int:
        """切片总数。"""
        if project is None or version_id is None:
            project, version_id, _ = self._get_project_version()
        if not project:
            return 0

        conn = None
        try:
            conn = self._get_db()
            cur = conn.execute(
                "SELECT COUNT(*) AS cnt FROM requirement_slices "
                "WHERE project = %s AND version_id = %s",
                (project, version_id),
            )
            row = cur.fetchone() or {}
            return int(row.get("cnt", 0))
        except Exception as e:
            _log.error("[SliceStore] count 失败: %s", e)
            return 0
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)


# 单例
requirement_slice_store = RequirementSliceStore()
