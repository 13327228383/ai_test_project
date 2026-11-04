# -*- coding: utf-8 -*-
"""节点哈希 / 变更日志 持久化 — 从 smart_figma_fetcher.py 拆出，零逻辑变更。"""

from typing import Any

from src.pipeline.steps.step1.figma_hasher import load_node_hashes, save_node_hashes
from src.pipeline.steps.step1.figma_node_cache import (
    load_change_log,
    save_change_log,
    record_change_session,
)
import logging

_log = logging.getLogger(__name__)


class _PersistenceMixin:
    """_load_node_hashes / _save_node_hashes / _load_change_log / _save_change_log / _record_change_session。"""

    def _load_node_hashes(self) -> dict[str, str]:
        """默认读 MySQL（figma_state_store）；仅在显式提供时导入该文件。"""
        try:
            return load_node_hashes(version_label=self._version_label)
        except Exception:
            # 回退：从旧 JSON 文件显式导入一次（作为显式导入源，不参与主链路兜底）
            try:
                if self._NODE_HASH_PATH and self._NODE_HASH_PATH.is_file():
                    return load_node_hashes(
                        self._NODE_HASH_PATH, version_label=self._version_label
                    )
            except Exception as _e:
                _log.debug(
                    "[persistence] _load_node_hashes 异常: %s", _e, exc_info=True
                )
            return {}

    def _save_node_hashes(self) -> None:
        """主事实源写入 MySQL；同时保留 JSON 作为显式导出物。"""
        save_node_hashes(
            self._CACHE_DIR,
            self._NODE_HASH_PATH,
            self._node_hashes,
            version_label=self._version_label,
        )

    def _load_change_log(self) -> dict[str, Any]:
        """默认读 MySQL；仅在显式提供时导入该文件。"""
        try:
            return load_change_log(version_label=self._version_label)
        except Exception:
            try:
                if self._CHANGE_LOG_PATH and self._CHANGE_LOG_PATH.is_file():
                    return load_change_log(
                        self._CHANGE_LOG_PATH, version_label=self._version_label
                    )
            except Exception as _e:
                _log.debug("[persistence] _load_change_log 异常: %s", _e, exc_info=True)
            return {"sessions": []}

    def _save_change_log(self) -> None:
        """主事实源写入 MySQL；同时保留 JSON 作为显式导出物。"""
        save_change_log(self._CHANGE_LOG_PATH, self._change_log)

    def _record_change_session(self, session_data: dict[str, Any]) -> None:
        """记录一次拉取会话的变更信息到变更日志（MySQL 主链路 + JSON 导出物）。"""
        record_change_session(self._CHANGE_LOG_PATH, self._change_log, session_data)
