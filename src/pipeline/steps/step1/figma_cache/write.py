# -*- coding: utf-8 -*-
"""
_FigmaCacheWriteMixin — 写入缓存方法：set。
"""

import json
from typing import Any
import logging

_log = logging.getLogger(__name__)


class _FigmaCacheWriteMixin:
    """写入缓存方法 mixin。"""

    def set(
        self, file_key: str, node_id: str, depth: int, response_data: dict[str, Any]
    ):
        """写入缓存。"""
        self._ensure_conn()
        project, version = self._scope()
        key = self._make_key(file_key, node_id, depth)
        json_str = json.dumps(response_data, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "REPLACE INTO %s "
                "(cache_key, project, version_label, file_key, node_id, depth, response_json, created_at, updated_at) "
                "VALUES (%%s, %%s, %%s, %%s, %%s, %%s, %%s, NOW(), NOW())"
                % self._TABLE,
                (key, project, version, file_key, node_id, depth, json_str),
            )
            self._conn.commit()
