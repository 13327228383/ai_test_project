# -*- coding: utf-8 -*-
"""交互式需求澄清领域服务（ClarificationService）—— 流水线强化专项 T3。

★ 职责：把 Step2 ambiguity_report 产出的高危歧义项从「只出报告」升级为
  「可交互问答闭环」：
    1. create_sessions: 歧义报告 → 持久化澄清会话（clarification_sessions）
    2. list_sessions / get_session: 查询会话与澄清问题
    3. submit_answer: 逐条回答（clarification_answers）→ 回填 requirements
       （operation / condition_expr / expected_feedback）→ status=answered
    4. release_gate: 解除生成闸门 → status=released
    5. is_gate_open: 闸门语义 = any(blocking=1 且 status != 'released')

★ 会话状态机（design §7.3）：open → answered → released；open → skipped（保持阻塞）。
★ 回填规则（design §8 待明确①默认口径，集中维护于 _build_backfill）：
    按歧义类型 + 问题关键词映射到 requirements 字段：
      missing_precondition → condition_expr；conflicting_field_semantics → expected_feedback；
      non_unique_expected → expected_feedback；undefined_entity → operation；
      unquantified_constraint → expected_feedback；cross_req_conflict → operation/condition_expr。

★ 依赖方向：领域服务，供 clarification_bp / ambiguity_report 复用；
  不反向依赖 backend 路由。DB 层复用 src.core.db_manager（MySQL），
  无 MySQL 环境自动降级内存 SQLite（对齐 case_upload_history 兼容模式，便于单测）。
"""
from __future__ import annotations
from src.core.guarded import guard

import json
import logging
import uuid
from typing import Any, Optional

_log = logging.getLogger(__name__)

# ── 会话状态机 ──
STATUS_OPEN = "open"
STATUS_ANSWERED = "answered"
STATUS_RELEASED = "released"
STATUS_SKIPPED = "skipped"
_VALID_STATUSES = frozenset({STATUS_OPEN, STATUS_ANSWERED, STATUS_RELEASED, STATUS_SKIPPED})

# ── 连接获取（MySQL 优先，失败降级文件持久化 SQLite，与 case_upload_history 同构）──
# ★ 2026-08-26 收敛：原 :memory: 内存库重启即丢，改为 offline_store 文件持久化 + 回放同步。


def _get_db():
    """返回 (conn, is_sqlite)；优先 MySQL，失败降级文件持久化 SQLite。★ 复用 offline_store 共享实现。"""
    from src.core.offline_store import get_db_compat

    return get_db_compat("clarification_service")


def _release_db(conn, is_sqlite: bool) -> None:
    """MySQL 连接归还池；SQLite 文件库保持常驻。★ 复用 offline_store 共享实现。"""
    from src.core.offline_store import release_db

    release_db(conn, is_sqlite)


def _is_duplicate_key_error(exc: Exception) -> bool:
    """判定是否唯一键冲突（MySQL 1062 / SQLite UNIQUE constraint）。

    ★ 2026-09-23：重复键是「语句级」错误，事务仍可继续提交 —— 调用方据此跳过该条
    而不是回滚整批（回滚会丢掉本批已建会话）。跨 MySQL / SQLite 两种后端通用。
    """
    code = getattr(exc, "args", [None])[0]
    if code == 1062:
        return True
    text = str(exc).lower()
    return (
        "duplicate entry" in text
        or "unique constraint" in text
        or "1062" in text
    )


def _ensure_tables(conn, is_sqlite: bool) -> None:
    """确保澄清两表存在（幂等；MySQL 侧表结构唯一事实源在 schema.sql 第46域）。"""
    if is_sqlite:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clarification_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL UNIQUE,
                project TEXT NOT NULL,
                version_id TEXT NOT NULL DEFAULT '',
                run_id TEXT NOT NULL DEFAULT '',
                req_id TEXT NOT NULL DEFAULT '',
                ambiguity_item_id TEXT NOT NULL DEFAULT '',
                ambiguity_type TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL DEFAULT 'medium',
                blocking INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                clarify_questions TEXT NOT NULL DEFAULT '[]',
                backfill_json TEXT,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clarification_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                question_index INTEGER NOT NULL,
                question TEXT NOT NULL DEFAULT '',
                answer TEXT NOT NULL DEFAULT '',
                answered_by TEXT NOT NULL DEFAULT '',
                answered_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.commit()
    else:
        # MySQL：表已由 schema.sql 管理；此处仅兜底幂等建表（IF NOT EXISTS 兼容旧库）。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clarification_sessions (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(64) NOT NULL,
                project VARCHAR(128) NOT NULL,
                version_id VARCHAR(128) NOT NULL DEFAULT '',
                run_id VARCHAR(64) NOT NULL DEFAULT '',
                req_id VARCHAR(128) NOT NULL DEFAULT '',
                ambiguity_item_id VARCHAR(64) NOT NULL DEFAULT '',
                ambiguity_type VARCHAR(64) NOT NULL DEFAULT '',
                severity VARCHAR(16) NOT NULL DEFAULT 'medium',
                blocking TINYINT NOT NULL DEFAULT 0,
                status VARCHAR(16) NOT NULL DEFAULT 'open',
                clarify_questions JSON NOT NULL,
                backfill_json JSON NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_clr_session_id (session_id),
                INDEX idx_clr_session (project, run_id, req_id),
                INDEX idx_clr_status (project, status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clarification_answers (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(64) NOT NULL,
                question_index INT NOT NULL,
                question VARCHAR(512) NOT NULL DEFAULT '',
                answer VARCHAR(1024) NOT NULL DEFAULT '',
                answered_by VARCHAR(128) NOT NULL DEFAULT '',
                answered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_clr_ans (session_id, question_index),
                INDEX idx_clr_ans_session (session_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )


def _enqueue_session_snapshot(session_id: str) -> None:
    """降级写后：把该会话在离线影子表的完整行快照登记待回放（覆盖式，保留最新状态）。"""
    with guard("_enqueue_session_snapshot: from src.core import offline_store", log=_log, level=logging.WARNING):
        from src.core import offline_store
        from src.core.offline_store import get_conn as get_offline_conn

        row = get_offline_conn().execute(
            "SELECT * FROM clarification_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if row is None:
            return
        offline_store.enqueue(
            "clarification_session", dict(row), unique_key=f"session|{session_id}"
        )


def _enqueue_answer_snapshot(session_id: str, question_index: int) -> None:
    """降级写后：把该条回答快照登记待回放（覆盖式）。"""
    with guard("_enqueue_answer_snapshot: from src.core import offline_store", log=_log, level=logging.WARNING):
        from src.core import offline_store
        from src.core.offline_store import get_conn as get_offline_conn

        row = get_offline_conn().execute(
            "SELECT * FROM clarification_answers WHERE session_id=? AND question_index=?",
            (session_id, question_index),
        ).fetchone()
        if row is None:
            return
        offline_store.enqueue(
            "clarification_answer",
            dict(row),
            unique_key=f"answer|{session_id}|{question_index}",
        )


def _replay_session(payload: dict) -> bool:
    """把一条会话快照用 MySQL 直写回填（session_id 唯一键 upsert，不回走降级）。"""
    try:
        from src.core.db_manager import get_connection  # type: ignore

        conn = get_connection()
    except Exception:
        return False
    try:
        _ensure_tables(conn, False)
        q = payload.get("clarify_questions") or "[]"
        if isinstance(q, (list, dict)):
            q = json.dumps(q, ensure_ascii=False)
        bf = payload.get("backfill_json") or None
        conn.execute(
            "INSERT INTO clarification_sessions "
            "(session_id, project, version_id, run_id, req_id, ambiguity_item_id, "
            " ambiguity_type, severity, blocking, status, clarify_questions, backfill_json) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE status=VALUES(status), "
            "backfill_json=VALUES(backfill_json), clarify_questions=VALUES(clarify_questions)",
            (
                payload.get("session_id", ""),
                payload.get("project", ""),
                payload.get("version_id", ""),
                payload.get("run_id", ""),
                payload.get("req_id", ""),
                payload.get("ambiguity_item_id", ""),
                payload.get("ambiguity_type", ""),
                payload.get("severity", "medium"),
                1 if payload.get("blocking") else 0,
                payload.get("status", STATUS_OPEN),
                q,
                bf,
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        _log.warning("[clarification_service] 回放会话失败: %s", e)
        return False
    finally:
        from src.core.db_manager import release_connection  # type: ignore

        try:
            release_connection(conn)
        except Exception:
            with guard("_replay_session: conn.close()", log=_log):
                conn.close()


def _replay_answer(payload: dict) -> bool:
    """把一条回答快照用 MySQL 直写回填（session_id+question_index 唯一键 upsert）。"""
    try:
        from src.core.db_manager import get_connection  # type: ignore

        conn = get_connection()
    except Exception:
        return False
    try:
        _ensure_tables(conn, False)
        conn.execute(
            "INSERT INTO clarification_answers "
            "(session_id, question_index, question, answer, answered_by, answered_at) "
            "VALUES (%s,%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE question=VALUES(question), answer=VALUES(answer), "
            "answered_by=VALUES(answered_by), answered_at=VALUES(answered_at)",
            (
                payload.get("session_id", ""),
                payload.get("question_index", 0),
                payload.get("question", ""),
                payload.get("answer", ""),
                payload.get("answered_by", ""),
                payload.get("answered_at", ""),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        _log.warning("[clarification_service] 回放回答失败: %s", e)
        return False
    finally:
        from src.core.db_manager import release_connection  # type: ignore

        try:
            release_connection(conn)
        except Exception:
            with guard("_replay_answer: conn.close()", log=_log):
                conn.close()


def _build_backfill_update(
    backfill: dict[str, str], req_id: str, project: str = "", version_id: str = ""
) -> tuple[str, list[Any]] | None:
    """构造 requirements 回填 UPDATE（MySQL %s 占位）；无可回填字段时返回 None。

    ★ 供 _backfill_requirement（MySQL 在线路径）与 _replay_backfill（离线回放路径）复用，
      消除两处 SQL 拼接重复。requirements 表列名为 condition_expr（非 condition）。
    """
    sets: list[str] = []
    params: list[Any] = []
    if backfill.get("operation"):
        sets.append("operation=%s")
        params.append(backfill["operation"])
    if backfill.get("condition"):
        sets.append("condition_expr=%s")
        params.append(backfill["condition"])
    if backfill.get("expected_feedback"):
        sets.append("expected_feedback=%s")
        params.append(backfill["expected_feedback"])
    if not sets:
        return None
    where = ["req_id=%s"]
    params.append(req_id)
    if project:
        where.append("project=%s")
        params.append(project)
    # version_id 为数字时追加过滤（requirements.version_id 是 BIGINT，
    # 非数字标识如 "v1" 会触发 MySQL 截断告警，故跳过避免误伤）。
    if version_id:
        try:
            int(version_id)
            where.append("version_id=%s")
            params.append(version_id)
        except (TypeError, ValueError) as _e:
            _log.debug("_build_backfill_update best-effort 容忍: %s", str(_e)[:120])
    sql = "UPDATE requirements SET %s WHERE %s" % (", ".join(sets), " AND ".join(where))
    return sql, params


def _enqueue_backfill(
    req_id: str,
    backfill: dict[str, str],
    project: str = "",
    version_id: str = "",
) -> None:
    """离线：把 requirements 回填登记待回放（requirements 表不在降级 SQLite 中）。"""
    with guard("_enqueue_backfill: from src.core import offline_store", log=_log, level=logging.WARNING):
        from src.core import offline_store

        offline_store.enqueue(
            "requirement_backfill",
            {
                "req_id": req_id,
                "project": project or "",
                "version_id": version_id or "",
                "backfill": dict(backfill or {}),
            },
            unique_key=f"backfill|{project or ''}|{req_id}",
        )


def _replay_backfill(payload: dict) -> bool:
    """把一条 requirements 回填用 MySQL 直写回填（不回走降级路径，避免重入）。"""
    try:
        from src.core.db_manager import get_connection  # type: ignore

        conn = get_connection()
    except Exception:
        return False
    try:
        req_id = str(payload.get("req_id") or "")
        backfill = payload.get("backfill") or {}
        if isinstance(backfill, str):
            try:
                backfill = json.loads(backfill)
            except Exception:
                backfill = {}
        project = str(payload.get("project") or "")
        version_id = str(payload.get("version_id") or "")
        if not req_id or not backfill:
            return True  # 无有效内容，视为已处理，避免反复重放
        result = _build_backfill_update(backfill, req_id, project, version_id)
        if result is None:
            return True
        sql, params = result
        conn.execute(sql, params)
        conn.commit()
        return True
    except Exception as e:
        _log.warning("[clarification_service] 回放回填失败: %s", e)
        return False
    finally:
        from src.core.db_manager import release_connection  # type: ignore

        try:
            release_connection(conn)
        except Exception:
            with guard("_replay_backfill: conn.close()", log=_log):
                conn.close()


def _maybe_replay_all() -> None:
    """MySQL 可用时的机会性回放：把离线积压的会话/回答/回填同步回 MySQL。"""
    with guard("_maybe_replay_all: from src.core import offline_store", log=_log):
        from src.core import offline_store

        offline_store.drain("clarification_session", _replay_session)
        offline_store.drain("clarification_answer", _replay_answer)
        offline_store.drain("requirement_backfill", _replay_backfill)


def _now_iso() -> str:
    """ISO8601 UTC 时间（带 Z，用于对外 JSON 响应，对齐 ledger 时间格式约定）。"""
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _now_mysql() -> str:
    """MySQL DATETIME 格式时间（YYYY-MM-DD HH:MM:SS，用于 DB 列写入）。"""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _session_to_dict(row) -> dict[str, Any]:
    """MySQL/SQLite 行 → 会话 dict（对外 JSON 同构）。"""
    questions = row.get("clarify_questions") or []
    if isinstance(questions, str):
        try:
            questions = json.loads(questions)
        except Exception:
            questions = []
    backfill = row.get("backfill_json")
    if isinstance(backfill, str):
        try:
            backfill = json.loads(backfill)
        except Exception:
            backfill = None
    return {
        "session_id": row.get("session_id", ""),
        "project": row.get("project", ""),
        "version_id": row.get("version_id", ""),
        "run_id": row.get("run_id", ""),
        "req_id": row.get("req_id", ""),
        "ambiguity_item_id": row.get("ambiguity_item_id", ""),
        "ambiguity_type": row.get("ambiguity_type", ""),
        "severity": row.get("severity", "medium"),
        "blocking": bool(row.get("blocking", 0)),
        "status": row.get("status", STATUS_OPEN),
        "clarify_questions": questions,
        "backfill": backfill,
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


class ClarificationService:
    """交互式需求澄清领域服务（会话持久化 + 回答回填 + 闸门解除）。"""

    # ─────────────────────────────────────────────────────────────────
    # 会话创建
    # ─────────────────────────────────────────────────────────────────
    def create_sessions(
        self,
        ambiguity_report: dict[str, Any],
        project: str,
        version_id: str = "",
        run_id: str = "",
        answered_by: str = "system",
    ) -> list[dict[str, Any]]:
        """把歧义报告中的高危（blocking）歧义项持久化为澄清会话。

        幂等：同 (project, run_id, ambiguity_item_id) 已存在则跳过（不重复建会话）。

        Args:
            ambiguity_report: build_ambiguity_report 的返回 dict。
            project: 项目名。
            version_id / run_id: 流水线上下文。
            answered_by: 创建者标识。

        Returns:
            本次新建的会话 dict 列表（已存在则不重复返回）。
        """
        items = ambiguity_report.get("items") or []
        if not items:
            return []

        conn, is_sqlite = _get_db()
        created: list[dict[str, Any]] = []
        try:
            _ensure_tables(conn, is_sqlite)
            # ★ MySQL 可用时机会性回放：把离线积压的会话/回答补回 MySQL。
            if not is_sqlite:
                _maybe_replay_all()
            # 仅持久化阻断生成（high）的歧义项 —— 低危只上报不建会话。
            seq = 0
            for item in items:
                if not item.get("blocking"):
                    continue
                seq += 1
                amb_id = str(item.get("ambiguity_id") or f"AMB-{seq:04d}")
                req_id = str(item.get("evidence", {}).get("req_id") or "")
                # ★ 2026-09-23 根治（Duplicate entry 'CLR-x-001' for key uk_clr_session_id）：
                #   根因（3 层）：
                #   ① session_id 由 f"CLR-{run_id or 'x'}-{seq:03d}" 生成 —— run_id 为空时
                #      退化成 `CLR-x-001`，而 uk_clr_session_id 是**全局唯一键**（不含 project），
                #      跨项目 / 跨批次第 2 次插入必然撞键；
                #   ② 幂等判据是 (project, run_id, ambiguity_item_id)，与唯一键不同源 →
                #      判"不存在"却依然插入 → 撞键；
                #   ③ 撞键抛异常后整批（含此前已建会话）被 `return []` 丢弃，澄清闭环静默瘫痪。
                #   修复：session_id 改为 (project, run_id, amb_id) 的确定性 UUIDv5 ——
                #   同输入恒同 ID（天然幂等），三元组参与散列（跨项目/跨批次天然不撞）。
                #   通用性：任何项目、任何 run_id、任何歧义项集合。
                session_id = "CLR-" + uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "clarification|%s|%s|%s" % (project, run_id, amb_id),
                ).hex[:12]

                # 幂等：同项目同 run 同歧义项不重复建
                exists = self._session_exists(conn, is_sqlite, project, run_id, amb_id)
                if exists:
                    continue

                questions = item.get("clarify_questions") or []
                q_json = json.dumps(questions, ensure_ascii=False)
                try:
                    if is_sqlite:
                        conn.execute(
                            "INSERT INTO clarification_sessions "
                            "(session_id, project, version_id, run_id, req_id, "
                            " ambiguity_item_id, ambiguity_type, severity, blocking, status, "
                            " clarify_questions, backfill_json) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)",
                            (
                                session_id,
                                project,
                                version_id,
                                run_id,
                                req_id,
                                amb_id,
                                item.get("type", ""),
                                item.get("severity", "high"),
                                1 if item.get("blocking") else 0,
                                STATUS_OPEN,
                                q_json,
                            ),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO clarification_sessions "
                            "(session_id, project, version_id, run_id, req_id, "
                            " ambiguity_item_id, ambiguity_type, severity, blocking, status, "
                            " clarify_questions, backfill_json) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)",
                            (
                                session_id,
                                project,
                                version_id,
                                run_id,
                                req_id,
                                amb_id,
                                item.get("type", ""),
                                item.get("severity", "high"),
                                1 if item.get("blocking") else 0,
                                STATUS_OPEN,
                                q_json,
                            ),
                        )
                except Exception as _ie:
                    # ★ 逐条隔离（2026-09-23）：单条撞键/写失败不得拖垮整批。
                    #   MySQL/SQLite 的重复键均为「语句级」错误，事务仍可继续提交，
                    #   故此处不 rollback（避免回滚掉本批已建会话），仅跳过该条。
                    if _is_duplicate_key_error(_ie):
                        _log.info(
                            "[clarification_service] 会话已存在，跳过: %s", session_id
                        )
                        continue
                    raise
                created.append(
                    {
                        "session_id": session_id,
                        "req_id": req_id,
                        "ambiguity_item_id": amb_id,
                        "ambiguity_type": item.get("type", ""),
                        "clarify_questions": questions,
                        "status": STATUS_OPEN,
                    }
                )
                # ★ 降级写入登记待回放：MySQL 恢复后自动同步新会话。
                if is_sqlite:
                    _enqueue_session_snapshot(session_id)
            conn.commit()
        except Exception as e:
            _log.warning("[clarification_service] create_sessions 失败: %s", e)
            return []
        finally:
            _release_db(conn, is_sqlite)

        _log.info(
            "[clarification_service] 已创建澄清会话 %d 个 (project=%s run=%s)",
            len(created),
            project,
            run_id,
        )
        return created

    # ─────────────────────────────────────────────────────────────────
    # 查询
    # ─────────────────────────────────────────────────────────────────
    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """按 session_id 查询会话（含已答问题）。"""
        conn, is_sqlite = _get_db()
        try:
            _ensure_tables(conn, is_sqlite)
            if not is_sqlite:
                _maybe_replay_all()
            row = self._fetch_session_row(conn, is_sqlite, session_id)
            if row is None:
                return None
            sess = _session_to_dict(row)
            sess["answers"] = self._list_answers(conn, is_sqlite, session_id)
            return sess
        finally:
            _release_db(conn, is_sqlite)

    def list_sessions(
        self, project: str, version_id: str = "", run_id: str = ""
    ) -> list[dict[str, Any]]:
        """列出某项目/版本/运行下的澄清会话（含每会话已答问题）。"""
        conn, is_sqlite = _get_db()
        try:
            _ensure_tables(conn, is_sqlite)
            if not is_sqlite:
                _maybe_replay_all()
            where = ["project=?"] if is_sqlite else ["project=%s"]
            params: list[Any] = [project]
            if version_id:
                where.append("version_id=?") if is_sqlite else where.append("version_id=%s")
                params.append(version_id)
            if run_id:
                where.append("run_id=?") if is_sqlite else where.append("run_id=%s")
                params.append(run_id)
            sql = "SELECT * FROM clarification_sessions WHERE %s ORDER BY id ASC" % " AND ".join(where)
            rows = conn.execute(sql, params).fetchall() or []
            out = []
            for r in rows:
                sess = _session_to_dict(r)
                sess["answers"] = self._list_answers(
                    conn, is_sqlite, sess["session_id"]
                )
                out.append(sess)
            return out
        finally:
            _release_db(conn, is_sqlite)

    # ─────────────────────────────────────────────────────────────────
    # 回答 + 回填 + 闸门
    # ─────────────────────────────────────────────────────────────────
    def submit_answer(
        self,
        session_id: str,
        answers: list[dict[str, Any]],
        answered_by: str = "",
    ) -> dict[str, Any]:
        """提交回答：逐条写入 clarification_answers，回填 requirements，状态→answered。

        Args:
            session_id: 会话 ID。
            answers: [{question_index, answer}, ...]。
            answered_by: 回答人（当前登录用户）。

        Returns:
            {"ok": bool, "session_status": str, "backfill": dict|None, "error": str|None}
        """
        if not answers:
            return {"ok": False, "session_status": "", "backfill": None, "error": "answers 为空"}

        conn, is_sqlite = _get_db()
        try:
            _ensure_tables(conn, is_sqlite)
            if not is_sqlite:
                _maybe_replay_all()
            row = self._fetch_session_row(conn, is_sqlite, session_id)
            if row is None:
                return {"ok": False, "session_status": "", "backfill": None, "error": "会话不存在"}

            # 逐条回答（upsert：同 session+question_index 覆盖，支持多轮修订）
            for a in answers:
                q_idx = int(a.get("question_index", 0))
                answer = str(a.get("answer") or "").strip()
                question = str(a.get("question") or "")
                if not answer:
                    continue
                if is_sqlite:
                    conn.execute(
                        "INSERT OR REPLACE INTO clarification_answers "
                        "(session_id, question_index, question, answer, answered_by, answered_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (session_id, q_idx, question, answer, answered_by or "unknown", _now_mysql()),
                    )
                else:
                    conn.execute(
                        "INSERT INTO clarification_answers "
                        "(session_id, question_index, question, answer, answered_by, answered_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE answer=VALUES(answer), "
                        "answered_by=VALUES(answered_by), answered_at=VALUES(answered_at)",
                        (session_id, q_idx, question, answer, answered_by or "unknown", _now_mysql()),
                    )

            # 回填 requirements（按歧义类型 + 问题语义映射）
            backfill = self._build_backfill(row, answers)
            req_id = str(row.get("req_id") or "")
            project = str(row.get("project") or "")
            version_id = str(row.get("version_id") or "")
            backfill_ok = False
            if req_id and backfill:
                backfill_ok = self._backfill_requirement(
                    req_id, backfill, project=project, version_id=version_id
                )

            # 更新会话状态
            backfill_json = json.dumps(backfill, ensure_ascii=False) if backfill else None
            if is_sqlite:
                conn.execute(
                    "UPDATE clarification_sessions SET status=?, backfill_json=?, "
                    "updated_at=? WHERE session_id=?",
                    (STATUS_ANSWERED, backfill_json, _now_mysql(), session_id),
                )
            else:
                conn.execute(
                    "UPDATE clarification_sessions SET status=%s, backfill_json=%s "
                    "WHERE session_id=%s",
                    (STATUS_ANSWERED, backfill_json, session_id),
                )
            conn.commit()

            # ★ 降级写入登记待回放：回答 + 会话状态一并同步回 MySQL。
            if is_sqlite:
                for a in answers:
                    _enqueue_answer_snapshot(session_id, int(a.get("question_index", 0)))
                _enqueue_session_snapshot(session_id)

            return {
                "ok": True,
                "session_status": STATUS_ANSWERED,
                "backfill": backfill,
                "backfill_ok": backfill_ok,
                "error": None,
            }
        except Exception as e:
            _log.warning("[clarification_service] submit_answer 失败: %s", e)
            return {"ok": False, "session_status": "", "backfill": None, "error": str(e)[:200]}
        finally:
            _release_db(conn, is_sqlite)

    def release_gate(self, session_id: str) -> dict[str, Any]:
        """解除某会话的生成闸门（status → released）。

        Returns:
            {"ok": bool, "gate": str, "can_generate": bool, "error": str|None}
        """
        conn, is_sqlite = _get_db()
        try:
            _ensure_tables(conn, is_sqlite)
            if not is_sqlite:
                _maybe_replay_all()
            row = self._fetch_session_row(conn, is_sqlite, session_id)
            if row is None:
                return {"ok": False, "gate": "unknown", "can_generate": False, "error": "会话不存在"}
            if is_sqlite:
                conn.execute(
                    "UPDATE clarification_sessions SET status=?, updated_at=? WHERE session_id=?",
                    (STATUS_RELEASED, _now_mysql(), session_id),
                )
            else:
                conn.execute(
                    "UPDATE clarification_sessions SET status=%s WHERE session_id=%s",
                    (STATUS_RELEASED, session_id),
                )
            conn.commit()
            # ★ 降级写入登记待回放：released 状态一并同步回 MySQL（否则恢复后仍停留在 answered）。
            if is_sqlite:
                _enqueue_session_snapshot(session_id)
            project = str(row.get("project") or "")
            run_id = str(row.get("run_id") or "")
            version_id = str(row.get("version_id") or "")
            still_open = self.is_gate_open(project, version_id, run_id)
            return {
                "ok": True,
                "gate": "released",
                "can_generate": not still_open,
                "error": None,
            }
        except Exception as e:
            _log.warning("[clarification_service] release_gate 失败: %s", e)
            return {"ok": False, "gate": "unknown", "can_generate": False, "error": str(e)[:200]}
        finally:
            _release_db(conn, is_sqlite)

    def is_gate_open(
        self, project: str, version_id: str = "", run_id: str = ""
    ) -> bool:
        """闸门语义：any(blocking=1 AND status != 'released')。"""
        conn, is_sqlite = _get_db()
        try:
            _ensure_tables(conn, is_sqlite)
            # ★ MySQL 可用时机会性回放：积压的 blocking 会话先同步回 MySQL，
            #   否则恢复后闸门会误判为「无阻塞」而放行未澄清需求。
            if not is_sqlite:
                _maybe_replay_all()
            where = ["project=?", "blocking=1", "status<>?"] if is_sqlite else ["project=%s", "blocking=1", "status<>%s"]
            params: list[Any] = [project, STATUS_RELEASED]
            if version_id:
                where.append("version_id=?") if is_sqlite else where.append("version_id=%s")
                params.append(version_id)
            if run_id:
                where.append("run_id=?") if is_sqlite else where.append("run_id=%s")
                params.append(run_id)
            sql = "SELECT COUNT(*) AS cnt FROM clarification_sessions WHERE %s" % " AND ".join(where)
            row = conn.execute(sql, params).fetchone()
            return bool(row and int(row.get("cnt") or 0) > 0)
        except Exception as e:
            _log.warning("[clarification_service] is_gate_open 查询失败: %s", e)
            return False
        finally:
            _release_db(conn, is_sqlite)

    # ─────────────────────────────────────────────────────────────────
    # 回填映射（design §8 待明确①默认口径，集中维护）
    # ─────────────────────────────────────────────────────────────────
    def _build_backfill(
        self, session_row, answers: list[dict[str, Any]]
    ) -> dict[str, str]:
        """根据歧义类型 + 回答文本构造 requirements 回填 dict。

        Returns:
            {"operation"?: str, "condition"?: str, "expected_feedback"?: str}
        """
        amb_type = str(session_row.get("ambiguity_type") or "")
        # 逐题回答按问题关键词归类
        # 优先级：先判「预期/结果/反馈/断言/表现」类（更接近可观察断言），
        # 再判「前置/条件/角色/权限/状态」类；其余归 operation 微调。
        cond_parts: list[str] = []
        exp_parts: list[str] = []
        op_parts: list[str] = []
        for a in answers:
            q = str(a.get("question") or "")
            ans = str(a.get("answer") or "").strip()
            if not ans:
                continue
            if any(k in q for k in ("预期", "结果", "反馈", "断言", "表现", "提示")):
                exp_parts.append(ans)
            elif any(k in q for k in ("前置", "条件", "角色", "权限", "状态")):
                cond_parts.append(ans)
            else:
                op_parts.append(ans)

        backfill: dict[str, str] = {}
        # 类型级默认映射
        if amb_type == "missing_precondition":
            if cond_parts:
                backfill["condition"] = "；".join(cond_parts)
            if exp_parts:
                backfill["expected_feedback"] = "；".join(exp_parts)
        elif amb_type in ("non_unique_expected", "unquantified_constraint"):
            if exp_parts:
                backfill["expected_feedback"] = "；".join(exp_parts)
            if cond_parts:
                backfill["condition"] = "；".join(cond_parts)
        elif amb_type in ("conflicting_field_semantics", "cross_req_conflict"):
            if exp_parts:
                backfill["expected_feedback"] = "；".join(exp_parts)
            if cond_parts:
                backfill["condition"] = "；".join(cond_parts)
            if op_parts:
                backfill["operation"] = "；".join(op_parts)
        elif amb_type == "undefined_entity":
            if op_parts:
                backfill["operation"] = "；".join(op_parts)
        else:
            if exp_parts:
                backfill["expected_feedback"] = "；".join(exp_parts)
            if cond_parts:
                backfill["condition"] = "；".join(cond_parts)
            if op_parts:
                backfill["operation"] = "；".join(op_parts)
        return backfill

    def _backfill_requirement(
        self,
        req_id: str,
        backfill: dict[str, str],
        project: str = "",
        version_id: str = "",
    ) -> bool:
        """把回答回填到 requirements 表（operation / condition_expr / expected_feedback）。

        注意：requirements 表列名为 condition_expr（非 condition），此处做映射。
        更新按 (project, req_id) 匹配（version_id 可选）；MySQL 不可用/表缺失时
        登记到 offline outbox 待回放（不再静默丢失），返回 False 不阻断回答提交。
        """
        if not req_id or not backfill:
            return False
        try:
            conn, is_sqlite = _get_db()
            try:
                _ensure_tables(conn, is_sqlite)
                # ★ 离线：requirements 表不在降级 SQLite 中，改为登记待回放，避免永久丢失。
                if is_sqlite:
                    _enqueue_backfill(req_id, backfill, project, version_id)
                    return False
                result = _build_backfill_update(backfill, req_id, project, version_id)
                if result is None:
                    return False
                sql, params = result
                cur = conn.execute(sql, params)
                affected = getattr(cur, "rowcount", 0)
                conn.commit()
                if affected:
                    _log.info(
                        "[clarification_service] requirements 已回填 req_id=%s 字段=%s",
                        req_id,
                        ",".join(backfill.keys()),
                    )
                return bool(affected)
            finally:
                _release_db(conn, is_sqlite)
        except Exception as e:
            _log.warning(
                "[clarification_service] requirements 回填失败(不阻断) req_id=%s: %s",
                req_id,
                str(e)[:160],
            )
            return False

    # ─────────────────────────────────────────────────────────────────
    # 内部 DB 辅助
    # ─────────────────────────────────────────────────────────────────
    def _session_exists(
        self, conn, is_sqlite: bool, project: str, run_id: str, amb_id: str
    ) -> bool:
        try:
            if is_sqlite:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM clarification_sessions "
                    "WHERE project=? AND run_id=? AND ambiguity_item_id=?",
                    (project, run_id, amb_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM clarification_sessions "
                    "WHERE project=%s AND run_id=%s AND ambiguity_item_id=%s",
                    (project, run_id, amb_id),
                ).fetchone()
            return bool(row and int(row.get("cnt") or 0) > 0)
        except Exception:
            return False

    def _fetch_session_row(self, conn, is_sqlite: bool, session_id: str):
        try:
            if is_sqlite:
                return conn.execute(
                    "SELECT * FROM clarification_sessions WHERE session_id=?", (session_id,)
                ).fetchone()
            return conn.execute(
                "SELECT * FROM clarification_sessions WHERE session_id=%s", (session_id,)
            ).fetchone()
        except Exception:
            return None

    def _list_answers(self, conn, is_sqlite: bool, session_id: str) -> list[dict[str, Any]]:
        try:
            if is_sqlite:
                rows = conn.execute(
                    "SELECT * FROM clarification_answers WHERE session_id=? ORDER BY question_index ASC",
                    (session_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM clarification_answers WHERE session_id=%s "
                    "ORDER BY question_index ASC",
                    (session_id,),
                ).fetchall()
            out = []
            for r in rows or []:
                out.append(
                    {
                        "question_index": r.get("question_index", 0),
                        "question": r.get("question", ""),
                        "answer": r.get("answer", ""),
                        "answered_by": r.get("answered_by", ""),
                        "answered_at": str(r.get("answered_at") or ""),
                    }
                )
            return out
        except Exception:
            return []
