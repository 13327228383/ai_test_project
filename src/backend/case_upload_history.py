# -*- coding: utf-8 -*-
"""用例上传历史记录（T08 — 上传追溯）。

★ 设计：复用项目现有 MySQL 连接模式（src.core.db_manager），表 `case_upload_history`。
  字段对齐 §3 CaseUploadHistory：id / filename / target_key / version / uploader /
  uploaded_at / matched / policy / message / exec_summary。

★ 流水线强化专项 T0（2026-08-24）：本表已纳入 schema.sql 第45域统一管理（唯一事实源，
  与下方兜底定义逐字段一致：9 字段 + 3 索引 + InnoDB/utf8mb4 + 字段级/表级 COMMENT）。
  本模块自带 CREATE TABLE IF NOT EXISTS 兜底**保留为兼容层**：仅兼容未执行 schema.sql
  的旧库；schema 已建表环境下 _ensure_table 幂等不报错、不覆盖既有表结构。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Optional

_log = logging.getLogger(__name__)

# ── 连接获取：优先复用项目 MySQL 连接（src.core.db_manager / src.models.base），
#    无 MySQL 环境（如单测）自动降级为**文件持久化 SQLite**（src.core.offline_store），
#    保证历史功能可用且不阻断；MySQL 恢复后机会性回放（offline_store.drain）自动补回。
# ★ 2026-08-26 收敛：原 :memory: 内存库重启即丢，现已改为文件持久化 + 回放同步。


def _get_db():
    """返回 (conn, is_sqlite)；优先 MySQL，失败降级文件持久化 SQLite。★ 复用 offline_store 共享实现。"""
    from src.core.offline_store import get_db_compat

    return get_db_compat("case_upload_history")


def _release_db(conn, is_sqlite: bool) -> None:
    """MySQL 连接归还池；SQLite 文件库保持常驻。★ 复用 offline_store 共享实现。"""
    from src.core.offline_store import release_db

    release_db(conn, is_sqlite)


def _ensure_table(conn, is_sqlite: bool) -> None:
    """确保 case_upload_history 表存在（幂等，兼容 MySQL / SQLite 语法）。

    ★ T0 兼容层：表结构唯一事实源已迁移至 src/core/schema.sql 第45域；
      本函数仅作为旧库/降级环境的兜底（CREATE TABLE IF NOT EXISTS 幂等，
      不覆盖 schema.sql 已建表结构，仅当表缺失时按同构定义创建）。
    """
    if is_sqlite:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS case_upload_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL DEFAULT '',
                target_key TEXT NOT NULL DEFAULT '',
                version TEXT NOT NULL DEFAULT '',
                uploader TEXT NOT NULL DEFAULT '',
                uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                matched INTEGER NOT NULL DEFAULT 1,
                policy TEXT NOT NULL DEFAULT 'reject',
                message TEXT,
                exec_summary TEXT
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS case_upload_history (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                filename VARCHAR(255) NOT NULL DEFAULT '',
                target_key VARCHAR(128) NOT NULL DEFAULT '',
                version VARCHAR(64) NOT NULL DEFAULT '',
                uploader VARCHAR(128) NOT NULL DEFAULT '',
                uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                matched TINYINT NOT NULL DEFAULT 1,
                policy VARCHAR(16) NOT NULL DEFAULT 'reject',
                message TEXT,
                exec_summary TEXT,
                INDEX idx_cuh_target (target_key),
                INDEX idx_cuh_uploader (uploader),
                INDEX idx_cuh_time (uploaded_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )


def _replay_upload(payload: dict) -> bool:
    """把一条待回放的上传历史用 MySQL 直写回填（不回走降级路径，避免重入）。

    ★ 原表无唯一键，用业务键近似查重以降低重复回放；边跑边删由 offline_store.drain 保证。
    """
    try:
        from src.core.db_manager import get_connection  # type: ignore
        conn = get_connection()
    except Exception:
        return False
    try:
        _ensure_table(conn, False)
        exists = conn.execute(
            "SELECT COUNT(*) AS c FROM case_upload_history "
            "WHERE filename=%s AND target_key=%s AND version=%s AND uploader=%s "
            "AND message=%s AND exec_summary=%s",
            (
                payload.get("filename", ""),
                payload.get("target_key", ""),
                payload.get("version", ""),
                payload.get("uploader", "unknown"),
                payload.get("message", ""),
                payload.get("exec_summary", ""),
            ),
        ).fetchone()
        if exists and int(exists.get("c") or 0) > 0:
            return True
        conn.execute(
            "INSERT INTO case_upload_history "
            "(filename, target_key, version, uploader, matched, policy, message, exec_summary) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                payload.get("filename", ""),
                payload.get("target_key", ""),
                payload.get("version", ""),
                payload.get("uploader", "unknown"),
                payload.get("matched", 1),
                payload.get("policy", "reject"),
                payload.get("message", ""),
                payload.get("exec_summary", ""),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        _log.warning("[case_upload_history] 回放失败: %s", e)
        return False
    finally:
        from src.core.db_manager import release_connection  # type: ignore
        try:
            release_connection(conn)
        except Exception:
            with guard("_replay_upload: conn.close()", log=_log):
                conn.close()


def _maybe_replay() -> None:
    """MySQL 可用时的机会性回放：把离线积压的上传历史同步回 MySQL。"""
    with guard("_maybe_replay: from src.core import offline_store", log=_log):
        from src.core import offline_store
        offline_store.drain("case_upload_history", _replay_upload)


def record(
    filename: str,
    target_key: str,
    version: str,
    uploader: str,
    matched: bool,
    policy: str,
    message: str,
    exec_summary: str,
) -> Optional[dict]:
    """写入一条上传历史记录（模块级函数，供路由直接调用）。

    Returns:
        写入后的记录 dict（含 id），失败返回 None。
    """
    try:
        conn, is_sqlite = _get_db()
        try:
            _ensure_table(conn, is_sqlite)
            # ★ MySQL 可用时机会性回放：把离线积压的上传历史补回 MySQL。
            if not is_sqlite:
                _maybe_replay()
            placeholder = "?" if is_sqlite else "%s"
            cur = conn.execute(
                f"""
                INSERT INTO case_upload_history
                (filename, target_key, version, uploader, matched, policy, message, exec_summary)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                """,
                (
                    filename or "",
                    target_key or "",
                    version or "",
                    uploader or "unknown",
                    1 if matched else 0,
                    policy or "reject",
                    message or "",
                    exec_summary or "",
                ),
            )
            # 兼容 MySQL(SteadyDB) / SQLite：优先 cursor.lastrowid，回退底层 connection
            try:
                rid = cur.lastrowid
            except Exception:
                rid = getattr(conn, "lastrowid", None) or getattr(
                    getattr(conn, "connection", None), "insert_id", 0
                )()
            conn.commit()
            # ★ 降级写入登记待回放：MySQL 恢复后自动同步，避免离线数据永久分叉。
            if is_sqlite:
                from src.core import offline_store
                offline_store.enqueue(
                    "case_upload_history",
                    {
                        "filename": filename or "",
                        "target_key": target_key or "",
                        "version": version or "",
                        "uploader": uploader or "unknown",
                        "matched": 1 if matched else 0,
                        "policy": policy or "reject",
                        "message": message or "",
                        "exec_summary": exec_summary or "",
                    },
                    unique_key=f"upload|{target_key or ''}|{version or ''}|{rid}",
                )
            _log.info(
                "[case_upload_history] 已记录 id=%s target=%s version=%s matched=%s",
                rid, target_key, version, matched,
            )
            return {
                "id": rid,
                "filename": filename,
                "target_key": target_key,
                "version": version,
                "uploader": uploader,
                "matched": matched,
                "policy": policy,
                "message": message,
                "exec_summary": exec_summary,
            }
        finally:
            _release_db(conn, is_sqlite)
    except Exception as e:
        _log.warning("[case_upload_history] 写入失败: %s", e)
        return None


class CaseUploadHistory:
    """上传历史记录门面类（与 routes 中 `from ... import CaseUploadHistory` 对齐）。"""

    @staticmethod
    def record(
        filename: str,
        target_key: str,
        version: str,
        uploader: str,
        matched: bool,
        policy: str,
        message: str,
        exec_summary: str,
    ) -> Optional[dict]:
        return record(
            filename=filename,
            target_key=target_key,
            version=version,
            uploader=uploader,
            matched=matched,
            policy=policy,
            message=message,
            exec_summary=exec_summary,
        )

    @staticmethod
    def list_recent(limit: int = 50, target_key: str = "") -> list[dict]:
        return list_recent(limit=limit, target_key=target_key)


def list_recent(limit: int = 50, target_key: str = "") -> list[dict]:
    """查询最近上传历史。

    Args:
        limit: 返回条数上限（最大 200）。
        target_key: 按被测系统过滤（可选）。
    Returns:
        历史记录 list（按时间倒序）。
    """
    limit = max(1, min(int(limit), 200))
    try:
        conn, is_sqlite = _get_db()
        try:
            _ensure_table(conn, is_sqlite)
            # ★ MySQL 可用时机会性回放：把离线积压补回 MySQL，再读最新数据。
            if not is_sqlite:
                _maybe_replay()
            if target_key:
                rows = conn.execute(
                    "SELECT * FROM case_upload_history WHERE target_key=? "
                    "ORDER BY id DESC LIMIT ?",
                    (target_key, limit),
                ).fetchall() if is_sqlite else conn.execute(
                    "SELECT * FROM case_upload_history WHERE target_key=%s "
                    "ORDER BY id DESC LIMIT %s",
                    (target_key, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM case_upload_history ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall() if is_sqlite else conn.execute(
                    "SELECT * FROM case_upload_history ORDER BY id DESC LIMIT %s",
                    (limit,),
                ).fetchall()
            out = []
            for r in rows or []:
                out.append(
                    {
                        "id": r["id"],
                        "filename": r.get("filename", ""),
                        "target_key": r.get("target_key", ""),
                        "version": r.get("version", ""),
                        "uploader": r.get("uploader", ""),
                        "uploaded_at": str(r.get("uploaded_at", "")),
                        "matched": bool(r.get("matched", 1)),
                        "policy": r.get("policy", "reject"),
                        "message": r.get("message", ""),
                        "exec_summary": r.get("exec_summary", ""),
                    }
                )
            return out
        finally:
            _release_db(conn, is_sqlite)
    except Exception as e:
        _log.warning("[case_upload_history] 查询失败: %s", e)
        return []
