# -*- coding: utf-8 -*-
"""figma_api_quota 表读写 — 从 figma_state_store.py 拆出，零逻辑变更。"""

import json
import time
from pathlib import Path
from typing import Any
import logging

_log = logging.getLogger(__name__)


class _QuotaMixin:
    """save_quota / load_quota / count_quota + JSON 导入导出。"""

    # ------------------------------------------------------------------
    # figma_api_quota 表
    # ------------------------------------------------------------------
    def save_quota(self, data: dict[str, Any], version_label: str = "") -> bool:
        """写入/覆盖当前 Figma 凭证 + 月份的配额记录。version_label 仅兼容旧调用。"""
        if not isinstance(data, dict):
            return False
        try:
            identity = self._quota_identity()
            c = self._get_db()
            try:
                self._ensure_tables(c)
                current_month = data.get("current_month") or time.strftime("%Y-%m")
                total_used = int(data.get("total_used", 0) or 0)
                rate_limit_until = float(data.get("rate_limit_until", 0) or 0)
                last_call_at = float(data.get("last_call_at", 0) or 0)
                last_error = data.get("last_error")
                c.execute(
                    """
                    REPLACE INTO figma_api_quota(
                        provider, credential_scope, credential_hash, current_month,
                        total_used, rate_limit_until, last_call_at,
                        last_error, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        identity["provider"],
                        identity["credential_scope"],
                        identity["credential_hash"],
                        current_month,
                        total_used,
                        rate_limit_until,
                        last_call_at,
                        last_error,
                        time.time(),
                    ),
                )
                c.commit()
                self._last_error = ""
                return True
            finally:
                c.close()
        except Exception as e:
            self._last_error = str(e)
            return False

    def load_quota(self, version_label: str = "") -> dict[str, Any]:
        """读取当前 Figma 凭证的最新配额记录。version_label 仅兼容旧调用。"""
        try:
            identity = self._quota_identity()
            c = self._get_db()
            try:
                self._ensure_tables(c)
                row = c.execute(
                    """
                    SELECT current_month, total_used, rate_limit_until,
                           last_call_at, last_error
                      FROM figma_api_quota
                     WHERE provider = %s AND credential_scope = %s AND credential_hash = %s
                     ORDER BY current_month DESC
                     LIMIT 1
                    """,
                    (
                        identity["provider"],
                        identity["credential_scope"],
                        identity["credential_hash"],
                    ),
                ).fetchone()
                if row is None:
                    return {}
                d = (
                    dict(row)
                    if isinstance(row, dict)
                    else {
                        "current_month": row[0],
                        "total_used": row[1],
                        "rate_limit_until": row[2],
                        "last_call_at": row[3],
                        "last_error": row[4],
                    }
                )
                return {
                    "current_month": d.get("current_month", ""),
                    "total_used": int(d.get("total_used", 0) or 0),
                    "rate_limit_until": float(d.get("rate_limit_until", 0) or 0),
                    "last_call_at": float(d.get("last_call_at", 0) or 0),
                    "last_error": d.get("last_error"),
                }
            finally:
                c.close()
        except Exception as e:
            self._last_error = str(e)
            return {}

    def count_quota(self) -> int:
        try:
            c = self._get_db()
            try:
                self._ensure_tables(c)
                row = c.execute(
                    "SELECT COUNT(*) AS cnt FROM figma_api_quota"
                ).fetchone()
                if row is None:
                    return 0
                cnt = row["cnt"] if isinstance(row, dict) else (row[0] if row else 0)
                return int(cnt or 0)
            finally:
                c.close()
        except Exception as _e:
            _log.debug("[quota] count_quota 异常: %s", _e, exc_info=True)
            return 0

    # ----- 显式 JSON 导入/导出（quota） -----
    def export_quota_to_json(self, path: str) -> bool:
        data = self.load_quota()
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True
        except Exception as e:
            self._last_error = str(e)
            return False

    def import_quota_from_json(self, path: str, version_label: str = "") -> bool:
        try:
            source = Path(path)
            if not source.is_file():
                return False
            data = json.loads(source.read_text(encoding="utf-8"))
            return self.save_quota(data, version_label=version_label)
        except Exception as e:
            self._last_error = str(e)
            return False
