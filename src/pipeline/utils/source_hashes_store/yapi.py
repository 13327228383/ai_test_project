# -*- coding: utf-8 -*-
"""
SourceHashesStore YApi mixin：
save_yapi_hash, load_yapi_hash,
export_yapi_to_json, import_yapi_from_json。
"""

import json
from src.core.guarded import guard
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class _SourceHashesYapiMixin:
    """YApi 接口文档源数据哈希的读写与导入导出。"""

    # ------------------------------------------------------------------
    # YApi 哈希
    # ------------------------------------------------------------------
    def save_yapi_hash(self, overall_hash: str, version_label: str = "") -> bool:
        """写入 yapi 接口文档哈希。"""
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
            c.execute(
                "REPLACE INTO source_yapi_hashes "
                "(project, version_label, overall_hash, updated_at) "
                "VALUES (%s,%s,%s,NOW())",
                (self._project(), version_label or "", overall_hash or ""),
            )
            c.commit()
            return True
        except Exception as e:
            self._last_error = "save_yapi_hash failed: %s" % str(e)
            logger.error("[SourceHashes] save_yapi_hash failed: %s", e, exc_info=True)
            return False
        finally:
            if c:
                with guard("save_yapi_hash: c.close()", log=logger, level=logging.WARNING):
                    c.close()

    def load_yapi_hash(self, version_label: str = "") -> dict[str, Any]:
        """读取 yapi 接口文档哈希。

        返回 ``{"overall": "xxx", "updated_at": N}``，MySQL 为空时
        返回 ``{"overall": "", "updated_at": None}``。
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
            cur = c.execute(
                "SELECT overall_hash, updated_at FROM source_yapi_hashes "
                "WHERE project = %s AND version_label = %s",
                (self._project(), version_label or ""),
            )
            row = cur.fetchone()
        except Exception as e:
            self._last_error = "load_yapi_hash failed: %s" % str(e)
            return {"overall": "", "updated_at": None}
        finally:
            with guard("load_yapi_hash: c.close()", log=logger, level=logging.WARNING):
                c.close()

        if not row:
            return {"overall": "", "updated_at": None}
        return {"overall": row[0] or "", "updated_at": row[1]}

    # ------------------------------------------------------------------
    # 显式导入 / 导出（JSON 仅作为附件，不参与主链路兜底）
    # ------------------------------------------------------------------
    def export_yapi_to_json(self, path: str, version_label: str = "") -> bool:
        data = self.load_yapi_hash(version_label=version_label)
        if not data.get("overall"):
            return False
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "_overall": data.get("overall", ""),
                "updated_at": data.get("updated_at"),
            }
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except Exception as e:
            self._last_error = "export_yapi_to_json failed: %s" % str(e)
            return False

    def import_yapi_from_json(self, path: str, version_label: str = "") -> bool:
        try:
            source = Path(path)
            if not source.is_file():
                return False
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return False
            overall = str(data.get("_overall", data.get("overall", "")) or "")
            return self.save_yapi_hash(overall, version_label=version_label)
        except Exception as e:
            self._last_error = "import_yapi_from_json failed: %s" % str(e)
            return False
