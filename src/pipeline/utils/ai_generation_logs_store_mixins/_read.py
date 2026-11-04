# -*- coding: utf-8 -*-
from __future__ import annotations

"""
AiGenerationLogsStore 读取 Mixin
=================================
提供 ``load_logs`` / ``count`` 等 AI 生成日志读取方法。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class AiGenerationLogsStoreReadMixin:
    """AiGenerationLogsStore 读取能力。"""

    # -----------------------------------------------------------------
    # 读
    # -----------------------------------------------------------------
    def load_logs(
        self, classification: str | None = None, limit: int = 0
    ) -> list[dict[str, Any]]:
        """读取当前项目的 AI 生成日志。

        参数：
            classification: 可选过滤分类（env / hallucination / unknown），
                            None 表示不过滤
            limit:          返回条数上限，0 表示不限制
        """
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        except Exception as _e:
            _log.debug("[_read] load_logs 异常: %s", _e, exc_info=True)
            project = ""
        try:
            c = None
            c = None
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            if classification:
                rows = c.execute(
                    "SELECT * FROM ai_generation_logs WHERE project=%s AND classification=%s "
                    "ORDER BY created_at DESC" + (" LIMIT %s" if limit > 0 else ""),
                    (project, classification) + ((limit,) if limit > 0 else ()),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM ai_generation_logs WHERE project=%s ORDER BY created_at DESC"
                    + (" LIMIT %s" if limit > 0 else ""),
                    (project,) + ((limit,) if limit > 0 else ()),
                ).fetchall()
            # [FIX] c.close() moved to finally below
            self._last_error = ""
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                result.append(
                    {
                        "case_id": str(item.get("case_id", "") or ""),
                        "req_id": str(item.get("req_id", "") or ""),
                        "module": str(item.get("module", "") or ""),
                        "title": str(item.get("title", "") or ""),
                        "failure_reason": str(item.get("failure_reason", "") or ""),
                        "classification": str(
                            item.get("classification", "unknown") or "unknown"
                        ),
                        "timestamp": str(item.get("created_at", "") or ""),
                        "steps": self._from_json(item.get("raw_steps_json"), []),
                    }
                )
            return result
        except Exception as e:
            self._last_error = str(e)
            _log.info(
                "  [AiGenLogsStore] ai_generation_logs 读取失败: %s", str(e)[:120]
            )
            return []

        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("[_read] 非关键异常: %s", _e, exc_info=True)

    def count(self, classification: str | None = None) -> int:
        try:
            from src.core.config_loader import config

            project = getattr(config, "project_name", "") or ""
        except Exception as _e:
            _log.debug("[_read] count 异常: %s", _e, exc_info=True)
            project = ""
        try:
            c = None
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            if classification:
                row = c.execute(
                    "SELECT COUNT(*) AS cnt FROM ai_generation_logs WHERE project=%s AND classification=%s",
                    (project, classification),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT COUNT(*) AS cnt FROM ai_generation_logs WHERE project=%s",
                    (project,),
                ).fetchone()
            if row is None:
                return 0
            cnt = row["cnt"] if isinstance(row, dict) else (row[0] if row else 0)
            return int(cnt or 0)
        except Exception as _e:
            _log.debug("[_read] count 异常: %s", _e, exc_info=True)
            return 0
        finally:
            if c:
                try:
                    c.close()
                except Exception as _e:
                    _log.debug("conn.close 失败: %s", _e, exc_info=True)
