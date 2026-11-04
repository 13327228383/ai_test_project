# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
SourceHashesStore Breakdown mixin：
save_breakdown_hash, load_breakdown_hash,
export_breakdown_to_json, import_breakdown_from_json。
"""

import json
import logging
from pathlib import Path
from typing import Any

from .base import _HASH_VALUE_MAX_LEN, _OVERALL_KEY, _PAGE_KEY_PREFIX

logger = logging.getLogger(__name__)


class _SourceHashesBreakdownMixin:
    """Step2 需求拆解哈希的读写与导入导出。"""

    # ------------------------------------------------------------------
    # Breakdown 哈希
    # ------------------------------------------------------------------
    def save_breakdown_hash(
        self,
        overall_hash: str,
        page_hashes: dict[str, str] | None = None,
        meta: dict[str, Any] | None = None,
        version_label: str = "",
        conn=None,
    ) -> bool:
        """写入 Step2 需求拆解哈希（overall + 每页 page_id + meta）。

        ★ v2: 支持传入外部连接 conn，用于原子事务（如 step2.py 中先写细表再写统一表）。
        """
        c = None
        _own_conn = False
        try:
            if conn is not None:
                c = conn
            else:
                c = self._get_db()
                _own_conn = True
            self._ensure_tables(c)
            project = self._project()
            meta_json = self._to_json(meta) if meta else ""
            c.execute(
                "REPLACE INTO source_breakdown_hashes "
                "(project, version_label, hash_key, hash_value, updated_at, meta_json) "
                "VALUES (%s,%s,%s,%s,NOW(),%s)",
                (
                    project,
                    version_label or "",
                    _OVERALL_KEY,
                    overall_hash or "",
                    meta_json,
                ),
            )
            if page_hashes:
                rows = [
                    (
                        project,
                        version_label or "",
                        "%s%s" % (_PAGE_KEY_PREFIX, pid),
                        (hv or "")[:_HASH_VALUE_MAX_LEN],
                        meta_json,
                    )
                    for pid, hv in page_hashes.items()
                    if pid
                ]
                if rows:
                    c.executemany(
                        "REPLACE INTO source_breakdown_hashes "
                        "(project, version_label, hash_key, hash_value, updated_at, meta_json) "
                        "VALUES (%s,%s,%s,%s,NOW(),%s)",
                        rows,
                    )
            if _own_conn:
                c.commit()
            return True
        except Exception as e:
            self._last_error = "save_breakdown_hash failed: %s" % str(e)
            logger.error(
                "[SourceHashes] save_breakdown_hash failed: %s", e, exc_info=True
            )
            return False
        finally:
            if _own_conn and c:
                with guard("save_breakdown_hash: c.close()", log=logger, level=logging.WARNING):
                    c.close()

    def load_breakdown_hash(self, version_label: str = "") -> dict[str, Any]:
        """读取 Step2 需求拆解哈希。

        返回 ``{"overall": "xxx", "page_hashes": {...}, "meta": {...}, "updated_at": N}``。
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
                "SELECT hash_key, hash_value, updated_at, meta_json "
                "FROM source_breakdown_hashes "
                "WHERE project = %s AND version_label = %s",
                (project, version_label or ""),
            )
            rows = cur.fetchall()
        except Exception as e:
            self._last_error = "load_breakdown_hash failed: %s" % str(e)
            return {"overall": "", "page_hashes": {}, "meta": {}, "updated_at": None}
        finally:
            with guard("load_breakdown_hash: c.close()", log=logger, level=logging.WARNING):
                c.close()

        overall = ""
        page_hashes: dict[str, str] = {}
        meta: dict[str, Any] = {}
        updated_at: float | None = None
        for r in rows:
            if isinstance(r, dict):
                key = r["hash_key"]
                value = r["hash_value"]
                ts = r.get("updated_at")
                mj = r.get("meta_json", "")
            else:
                key, value, ts, mj = (
                    r[0],
                    r[1],
                    r[2] if len(r) > 2 else None,
                    r[3] if len(r) > 3 else "",
                )
            if key == _OVERALL_KEY:
                overall = value or ""
                updated_at = ts
                if mj:
                    parsed = self._from_json(mj, None)
                    if isinstance(parsed, dict):
                        meta = parsed
            elif isinstance(key, str) and key.startswith(_PAGE_KEY_PREFIX):
                page_hashes[key[len(_PAGE_KEY_PREFIX) :]] = value or ""
            elif isinstance(key, str):
                page_hashes[key] = value or ""
        return {
            "overall": overall,
            "page_hashes": page_hashes,
            "meta": meta,
            "updated_at": updated_at,
        }

    # ------------------------------------------------------------------
    # 显式导入 / 导出（JSON 仅作为附件，不参与主链路兜底）
    # ------------------------------------------------------------------
    def export_breakdown_to_json(self, path: str, version_label: str = "") -> bool:
        data = self.load_breakdown_hash(version_label=version_label)
        if (
            not data.get("overall")
            and not data.get("page_hashes")
            and not data.get("meta")
        ):
            return False
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "_overall": data.get("overall", ""),
                "pages": dict(data.get("page_hashes", {}) or {}),
                "meta": data.get("meta") or {},
                "updated_at": data.get("updated_at"),
            }
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            return True
        except Exception as e:
            self._last_error = "export_breakdown_to_json failed: %s" % str(e)
            return False

    def import_breakdown_from_json(self, path: str, version_label: str = "") -> bool:
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
            meta = data.get("meta")
            return self.save_breakdown_hash(
                overall, page_hashes=page_hashes, meta=meta, version_label=version_label
            )
        except Exception as e:
            self._last_error = "import_breakdown_from_json failed: %s" % str(e)
            return False
