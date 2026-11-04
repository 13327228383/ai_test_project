# -*- coding: utf-8 -*-
"""figma_change_sessions 表读写 — 从 figma_state_store.py 拆出，零逻辑变更。"""

import json
import time
from pathlib import Path
from typing import Any
import logging

_log = logging.getLogger(__name__)


class _ChangeLogMixin:
    """append_change_session / load_change_sessions / load_change_log / count + JSON 导入导出。"""

    # ------------------------------------------------------------------
    # figma_change_sessions 表
    # ------------------------------------------------------------------
    def append_change_session(
        self, session_data: dict[str, Any], version_label: str = ""
    ) -> bool:
        """追加一条会话（自动限长 50 条）。"""
        if not isinstance(session_data, dict):
            return False
        try:
            project = self._project()
            now = time.time()
            # 保证同一毫秒内多次调用也能生成唯一、单调递增的 session_id
            from . import FigmaStateStore  # noqa  延迟导入避免循环

            FigmaStateStore._call_counter = (
                getattr(FigmaStateStore, "_call_counter", 0) + 1
            )
            session_id = session_data.get("session_id") or "%020d%010d" % (
                int(now * 1000),
                FigmaStateStore._call_counter,
            )
            c = self._get_db()
            try:
                self._ensure_tables(c)
                c.execute(
                    """
                    REPLACE INTO figma_change_sessions(
                        project, version_label, session_id, session_json, created_at
                    ) VALUES (%s, %s, %s, %s, UNIX_TIMESTAMP())
                    """,
                    (
                        project,
                        version_label,
                        session_id,
                        self._to_json(session_data),
                    ),
                )
                # 超限裁剪（派生表包一层绕过 MySQL 1093）
                c.execute(
                    """
                    DELETE FROM figma_change_sessions
                     WHERE project = %s AND version_label = %s
                       AND session_id NOT IN (
                           SELECT session_id FROM (
                               SELECT session_id
                                 FROM figma_change_sessions
                                WHERE project = %s AND version_label = %s
                                ORDER BY created_at DESC, session_id DESC
                                LIMIT %s
                           ) AS _t
                       )
                    """,
                    (
                        project,
                        version_label,
                        project,
                        version_label,
                        self._MAX_SESSIONS,
                    ),
                )
                c.commit()
                self._last_error = ""
                return True
            finally:
                c.close()
        except Exception as e:
            self._last_error = str(e)
            _log.error(
                "[change_log] append_change_session failed: %s", e, exc_info=True
            )
            return False

    def load_change_sessions(self, version_label: str = "") -> list[dict[str, Any]]:
        """返回按时间升序排列的会话列表，与旧 JSON 结构兼容：``{"sessions": [...]}``。"""
        try:
            project = self._project()
            c = self._get_db()
            try:
                self._ensure_tables(c)
                rows = c.execute(
                    """
                    SELECT session_json, created_at
                      FROM figma_change_sessions
                     WHERE project = %s AND version_label = %s
                     ORDER BY created_at ASC
                    """,
                    (project, version_label),
                ).fetchall()
                sessions: list[dict[str, Any]] = []
                for row in rows:
                    item = (
                        dict(row)
                        if isinstance(row, dict)
                        else {"session_json": row[0], "created_at": row[1]}
                    )
                    parsed = self._from_json(item.get("session_json"), {})
                    if isinstance(parsed, dict) and parsed:
                        sessions.append(parsed)
                return sessions
            finally:
                c.close()
        except Exception as e:
            self._last_error = str(e)
            return []

    def load_change_log(self, version_label: str = "") -> dict[str, Any]:
        """返回与旧 ``figma_change_log.json`` 结构一致的字典。"""
        sessions = self.load_change_sessions(version_label=version_label)
        return {
            "sessions": sessions,
            "last_updated": sessions[-1].get("created_at") if sessions else 0,
        }

    def get_latest_failed_nodes(self, version_label: str = "") -> list[str]:
        """获取最近一次包含失败节点记录的会话中的 ID 列表。

        若最新会话显式记录 ``failed_node_ids: []``，则返回空列表，
        表示上一轮重试已将失败节点全部解决。
        """
        sessions = self.load_change_sessions(version_label=version_label)
        for s in reversed(sessions):
            failed = s.get("failed_node_ids")
            if isinstance(failed, list):
                return failed
        return []

    def count_change_sessions(self) -> int:
        try:
            project = self._project()
            c = self._get_db()
            try:
                self._ensure_tables(c)
                row = c.execute(
                    "SELECT COUNT(*) AS cnt FROM figma_change_sessions WHERE project=%s",
                    (project,),
                ).fetchone()
                if row is None:
                    return 0
                cnt = row["cnt"] if isinstance(row, dict) else (row[0] if row else 0)
                return int(cnt or 0)
            finally:
                c.close()
        except Exception as _e:
            _log.debug("[change_log] count_change_sessions 异常: %s", _e, exc_info=True)
            return 0

    # ----- 显式 JSON 导入/导出（change_log） -----
    def export_change_log_to_json(self, path: str, version_label: str = "") -> bool:
        try:
            data = self.load_change_log(version_label=version_label)
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True
        except Exception as e:
            self._last_error = str(e)
            return False

    def import_change_log_from_json(self, path: str, version_label: str = "") -> bool:
        try:
            source = Path(path)
            if not source.is_file():
                return False
            data = json.loads(source.read_text(encoding="utf-8"))
            sessions = data.get("sessions", []) if isinstance(data, dict) else []
            if not isinstance(sessions, list):
                return False
            ok = True
            for s in sessions:
                if not isinstance(s, dict):
                    continue
                ok = self.append_change_session(s, version_label=version_label) and ok
            return ok
        except Exception as e:
            self._last_error = str(e)
            return False
