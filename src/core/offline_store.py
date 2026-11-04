# -*- coding: utf-8 -*-
"""离线兜底存储（文件持久化 SQLite）+ MySQL 恢复后自动回放同步（Outbox 模式）。

★ 背景与根因（2026-08-26）：
  项目里三处「MySQL 不可用 → 本地兜底」的降级路径，原先无一回填：
    - case_upload_history / clarification_service 用 ``:memory:`` 内存 SQLite，进程重启即丢；
    - metrics_collector 用同进程内存 list，同样重启即丢。
  结果：MySQL 挂掉期间写入的数据，MySQL 恢复后**永远不会**同步回去，两边永久分叉。

★ 本模块把这条链路的「持久化 + 回填」统一收口（通用、规则驱动、不针对特定业务打补丁）：
    1. 持久化：单文件 SQLite（默认 ``data/offline_store.sqlite``），重启不丢，替代 :memory:。
    2. 待回放队列：``offline_outbox`` 表，每条降级写入追加一条待回放记录，
       ``(kind, unique_key)`` 唯一约束做幂等去重（重复 enqueue 不产生重复回放）。
    3. drain(kind, replay_fn)：MySQL 恢复后，由各业务点在拿到 MySQL 连接时**机会性**触发，
       把积压记录逐条重放，replay_fn 返回 True 视为成功并删除，失败保留待下次。

★ 接入约定（各业务点复刻同构模式）：
    - 降级写入成功后，调用 enqueue(kind, payload, unique_key) 登记待回放；
    - 拿到 MySQL 连接成功路径上，调用 drain(kind, replay_fn) 把历史积压回填；
    - replay_fn(payload) 用「MySQL 直写」实现（不得再走降级路径，避免重入死循环）。

★ 文件路径可覆盖：环境变量 ``AITEST_OFFLINE_STORE``；默认 ``<项目根>/data/offline_store.sqlite``。
"""
from __future__ import annotations
from src.core.guarded import guard

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Callable

_log = logging.getLogger(__name__)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_DEFAULT_PATH = os.path.join("data", "offline_store.sqlite")


def _db_path() -> str:
    """兜底文件路径：环境变量优先，默认项目根 data/offline_store.sqlite。"""
    env = os.environ.get("AITEST_OFFLINE_STORE")
    if env:
        return env
    return _DEFAULT_PATH


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn() -> "sqlite3.Connection":
    """懒初始化持久化 SQLite（文件级，重启不丢），返回 dict 行（支持 .get()/索引）。

    ★ 与 MySQL DictCursor 行路径行为一致，下游无需感知数据源差异。
    """
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                path = _db_path()
                d = os.path.dirname(path)
                if d:
                    os.makedirs(d, exist_ok=True)
                _conn = sqlite3.connect(path, check_same_thread=False)
                _conn.row_factory = lambda cursor, row: {
                    col[0]: row[idx] for idx, col in enumerate(cursor.description or [])
                }
                _conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS offline_outbox (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        kind TEXT NOT NULL,
                        unique_key TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT '',
                        UNIQUE(kind, unique_key)
                    )
                    """
                )
                _conn.commit()
                _log.info("[offline_store] 持久化兜底库已就绪: %s", path)
    return _conn


def mysql_available() -> bool:
    """探测 MySQL 是否可达（借用连接 + SELECT 1，借用/查询失败均视为不可达）。"""
    try:
        from src.models.base import get_conn as _mysql_conn

        c = _mysql_conn()
        try:
            c.execute("SELECT 1").fetchone()
            return True
        finally:
            with guard("mysql_available: c.close()", log=_log):
                c.close()
    except Exception:
        return False


def get_db_compat(caller: str = "offline_store"):
    """统一「优先 MySQL、失败降级文件 SQLite」的连接获取（返回 (conn, is_sqlite)）。

    ★ 供 case_upload_history / clarification_service 等业务点复用，消除多处重复实现。
    ★ 注意：内层 ``from src.models.base import get_conn`` 必须用别名，避免遮蔽本模块级
      的 SQLite ``get_conn``，否则最终降级会错误地再次调用 MySQL 连接。
    """
    try:
        try:
            from src.core.db_manager import get_connection  # type: ignore

            return get_connection(), False
        except Exception:
            from src.models.base import get_conn as _mysql_get_conn  # type: ignore

            return _mysql_get_conn(), False
    except Exception as e:
        _log.warning("[%s] MySQL 不可用，降级文件 SQLite（离线回放）: %s", caller, e)
        return get_conn(), True


def release_db(conn, is_sqlite: bool) -> None:
    """统一连接归还：MySQL 归还池；SQLite 文件库保持常驻。"""
    if is_sqlite:
        return
    try:
        from src.core.db_manager import release_connection  # type: ignore

        release_connection(conn)
    except Exception:
        with guard("release_db: conn.close()", log=_log):
            conn.close()


def enqueue(kind: str, payload: dict[str, Any], unique_key: str) -> None:
    """登记/更新一条待回放记录（幂等 + 覆盖：kind+unique_key 冲突时保留最新快照）。

    ★ 覆盖语义：同一业务键多次写入（如「建会话→回答→解除闸门」同一 session 多阶段），
      只保留最后一次完整快照，回放时以最新状态为准，避免重复 upsert 旧状态。
    """
    with guard("enqueue: conn = get_conn()", log=_log, level=logging.WARNING):
        conn = get_conn()
        conn.execute(
            "INSERT INTO offline_outbox (kind, unique_key, payload_json, created_at) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(kind, unique_key) DO UPDATE SET "
            "payload_json=excluded.payload_json, created_at=excluded.created_at",
            (
                kind,
                unique_key,
                json.dumps(payload, ensure_ascii=False, default=str),
                _now(),
            ),
        )
        conn.commit()


def drain(kind: str, replay_fn: Callable[[dict[str, Any]], bool], limit: int = 200) -> int:
    """把 kind 的积压记录重放回 MySQL；replay_fn 返回 True 视为成功并删除，返回成功条数。

    Args:
        kind: 业务标识（如 "case_upload_history" / "clarification_session"）。
        replay_fn: payload → bool 的重放函数（内部须用 MySQL 直写，不得再次降级）。
        limit: 单次最多重放条数（防止一次性长时间阻塞）。
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, payload_json FROM offline_outbox WHERE kind=? ORDER BY id ASC LIMIT ?",
            (kind, limit),
        ).fetchall()
    except Exception as e:
        _log.warning("[offline_store] drain 查询失败 kind=%s: %s", kind, e)
        return 0

    ok = 0
    for r in rows:
        try:
            payload = json.loads(r["payload_json"])
        except Exception:
            payload = {}
        with guard("drain: if replay_fn(payload): conn.execute(", log=_log, level=logging.WARNING):
            if replay_fn(payload):
                conn.execute("DELETE FROM offline_outbox WHERE id=?", (r["id"],))
                ok += 1
                conn.commit()  # 逐条提交，崩溃时已回放的不会重复
    if ok:
        _log.info("[offline_store] drain kind=%s 回放了 %d 条", kind, ok)
    return ok


def pending_count(kind: str) -> int:
    """查询某 kind 的待回放积压数（供前端/健康检查展示）。"""
    try:
        conn = get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM offline_outbox WHERE kind=?", (kind,)
        ).fetchone()
        return int(row["c"]) if row else 0
    except Exception:
        return 0


def pending_summary() -> dict[str, int]:
    """按 kind 归类的积压汇总。"""
    try:
        conn = get_conn()
        rows = conn.execute(
            "SELECT kind, COUNT(*) AS c FROM offline_outbox GROUP BY kind"
        ).fetchall()
        return {r["kind"]: int(r["c"]) for r in rows}
    except Exception:
        return {}


def replay_all(limit: int = 200) -> dict[str, int]:
    """全局回放：把离线积压的全部 kind 一次性同步回 MySQL。

    ★ 供就绪探针（readyz）等「MySQL 恢复」时机触发，避免仅在业务写路径才机会性回放。
    ★ 懒加载各业务 replay_fn，规避 offline_store 与业务模块的循环 import。
    ★ 仅在 MySQL 可达时执行；不可达直接返回空结果，避免空耗一轮重放。
    """
    if not mysql_available():
        return {}

    def _upload(p: dict) -> bool:
        from src.backend.case_upload_history import _replay_upload  # type: ignore

        return _replay_upload(p)

    def _session(p: dict) -> bool:
        from src.pipeline.steps.step2.clarification_service import _replay_session  # type: ignore

        return _replay_session(p)

    def _answer(p: dict) -> bool:
        from src.pipeline.steps.step2.clarification_service import _replay_answer  # type: ignore

        return _replay_answer(p)

    def _backfill(p: dict) -> bool:
        from src.pipeline.steps.step2.clarification_service import _replay_backfill  # type: ignore

        return _replay_backfill(p)

    def _metric(p: dict) -> bool:
        from src.extensions.metrics_collector.persistence import _PersistenceMixin  # type: ignore

        return _PersistenceMixin()._replay_metric(p)

    jobs = [
        ("case_upload_history", _upload),
        ("clarification_session", _session),
        ("clarification_answer", _answer),
        ("requirement_backfill", _backfill),
        ("ai_health_metrics", _metric),
    ]
    result: dict[str, int] = {}
    for kind, fn in jobs:
        try:
            result[kind] = drain(kind, fn, limit=limit)
        except Exception as e:
            _log.warning("[offline_store] replay_all kind=%s 失败: %s", kind, e)
            result[kind] = 0
    return result