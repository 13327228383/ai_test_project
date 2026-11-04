# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
SourceHashesStore Confluence mixin：
save_confluence_hash, load_confluence_hash,
export_confluence_to_json, import_confluence_from_json。
"""

import json
import logging
from pathlib import Path
from typing import Any

from .base import _HASH_VALUE_MAX_LEN, _OVERALL_KEY, _PAGE_KEY_PREFIX

logger = logging.getLogger(__name__)


class _SourceHashesConfluenceMixin:
    """Confluence 源数据哈希的读写与导入导出。"""

    # ------------------------------------------------------------------
    # Confluence 哈希
    # ------------------------------------------------------------------
    def save_confluence_hash(
        self,
        overall_hash: str,
        page_hashes: dict[str, str] | None = None,
        version_label: str = "",
    ) -> bool:
        """写入 confluence 源数据哈希（overall + 每页 page_id）。"""
        c = None
        try:
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            project = self._project()
            c.execute(
                "REPLACE INTO source_confluence_hashes "
                "(project, version_label, hash_key, hash_value, updated_at) "
                "VALUES (%s,%s,%s,%s,NOW())",
                (project, version_label or "", _OVERALL_KEY, overall_hash or ""),
            )
            if page_hashes:
                rows = [
                    (
                        project,
                        version_label or "",
                        "%s%s" % (_PAGE_KEY_PREFIX, pid),
                        (hv or "")[:_HASH_VALUE_MAX_LEN],
                    )
                    for pid, hv in page_hashes.items()
                    if pid
                ]
                if rows:
                    c.executemany(
                        "REPLACE INTO source_confluence_hashes "
                        "(project, version_label, hash_key, hash_value, updated_at) "
                        "VALUES (%s,%s,%s,%s,NOW())",
                        rows,
                    )
            c.commit()
            return True
        except Exception as e:
            self._last_error = "save_confluence_hash failed: %s" % str(e)
            logger.error(
                "[SourceHashes] save_confluence_hash failed: %s", e, exc_info=True
            )
            return False
        finally:
            if c:
                with guard("save_confluence_hash: c.close()", log=logger, level=logging.WARNING):
                    c.close()

    def load_confluence_hash(self, version_label: str = "") -> dict[str, Any]:
        """读取 confluence 源数据哈希。

        返回 ``{"overall": "xxx", "page_hashes": {pid: hash}, "updated_at": N}``，
        MySQL 为空时返回 ``{"overall": "", "page_hashes": {}, "updated_at": None}``。
        """
        try:
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = None
            c = self._get_db()
            self._ensure_tables(c)
            project = self._project()
            cur = c.execute(
                "SELECT hash_key, hash_value, updated_at "
                "FROM source_confluence_hashes "
                "WHERE project = %s AND version_label = %s",
                (project, version_label or ""),
            )
            rows = cur.fetchall()
        except Exception as e:
            self._last_error = "load_confluence_hash failed: %s" % str(e)
            return {"overall": "", "page_hashes": {}, "updated_at": None}
        finally:
            with guard("load_confluence_hash: c.close()", log=logger, level=logging.WARNING):
                c.close()

        overall = ""
        page_hashes: dict[str, str] = {}
        updated_at: float | None = None
        for r in rows:
            key = r["hash_key"] if isinstance(r, dict) else r[0]
            value = r["hash_value"] if isinstance(r, dict) else r[1]
            ts = (
                r.get("updated_at")
                if isinstance(r, dict)
                else (r[2] if len(r) > 2 else None)
            )
            if key == _OVERALL_KEY:
                overall = value or ""
                updated_at = ts
            elif isinstance(key, str) and key.startswith(_PAGE_KEY_PREFIX):
                page_hashes[key[len(_PAGE_KEY_PREFIX) :]] = value or ""
            elif isinstance(key, str):
                page_hashes[key] = value or ""
        return {
            "overall": overall,
            "page_hashes": page_hashes,
            "updated_at": updated_at,
        }

    # ------------------------------------------------------------------
    # 显式导入 / 导出（JSON 仅作为附件，不参与主链路兜底）
    # ------------------------------------------------------------------
    def export_confluence_to_json(self, path: str, version_label: str = "") -> bool:
        data = self.load_confluence_hash(version_label=version_label)
        if not data.get("overall") and not data.get("page_hashes"):
            return False
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "_overall": data.get("overall", ""),
                "pages": dict(data.get("page_hashes", {}) or {}),
                "updated_at": data.get("updated_at"),
            }
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except Exception as e:
            self._last_error = "export_confluence_to_json failed: %s" % str(e)
            return False

    def import_confluence_from_json(self, path: str, version_label: str = "") -> bool:
        """从磁盘 JSON 回填到 MySQL（显式迁移路径，不参与主链路兜底）。"""
        try:
            source = Path(path)
            if not source.is_file():
                return False
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return False
            overall = str(data.get("_overall", data.get("overall", "")) or "")
            page_hashes_raw = data.get("pages", data.get("page_hashes", {}))
            page_hashes = {
                str(k): str(v)
                for k, v in (page_hashes_raw or {}).items()
                if isinstance(v, str)
            }
            return self.save_confluence_hash(
                overall, page_hashes=page_hashes, version_label=version_label
            )
        except Exception as e:
            self._last_error = "import_confluence_from_json failed: %s" % str(e)
            return False
