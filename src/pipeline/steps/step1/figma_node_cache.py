# -*- coding: utf-8 -*-
"""
Figma 节点缓存模块
==================
变更日志持久化 + 缓存读取辅助函数。

**事实源迁移：** 变更日志已从 ``figma_change_log.json`` 迁移至 MySQL
``figma_change_sessions`` 表（见 `src/pipeline/utils/figma_state_store.py`）。
JSON 文件不再参与 Step1 主运行链路兜底。
"""

import json
import time
from pathlib import Path
from typing import Any
import logging

_log = logging.getLogger(__name__)


def _default_store():
    """懒加载 figma_state_store，避免模块导入时触发 DB 连接失败。"""
    from src.pipeline.utils.figma_state_store import figma_state_store

    return figma_state_store


def load_change_log(path: Path = None, *, version_label: str = "") -> dict[str, Any]:
    """加载变更日志。

    事实源：MySQL ``figma_change_sessions`` 表；当显式提供 ``path`` 且文件存在时，
    尝试从该 JSON 文件导入（显式导入场景）。
    """
    if path is not None and Path(path).is_file():
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as _e:
            _log.debug("[figma_node_cache] load_change_log 异常: %s", _e, exc_info=True)
    try:
        return _default_store().load_change_log(version_label=version_label)
    except Exception as _e:
        _log.debug("[figma_node_cache] load_change_log 异常: %s", _e, exc_info=True)
        return {"sessions": []}


def save_change_log(path: Path, change_log: dict[str, Any]) -> None:
    """持久化变更日志。事实源为 MySQL ``figma_change_sessions`` 表。"""
    try:
        if isinstance(change_log, dict) and "sessions" in change_log:
            for s in change_log.get("sessions", []) or []:
                if isinstance(s, dict):
                    _default_store().append_change_session(
                        s,
                        version_label=(
                            change_log.get("version_label", "")
                            if isinstance(change_log, dict)
                            else ""
                        ),
                    )
    except Exception as _e:
        _log.debug("[figma_node_cache] save_change_log 异常: %s", _e, exc_info=True)


def record_change_session(
    path: Path, change_log: dict[str, Any], session_data: dict[str, Any]
) -> None:
    """记录一次拉取会话的变更信息到变更日志。最多保留 50 条。"""
    sessions = change_log.get("sessions", []) if isinstance(change_log, dict) else []
    sessions.append(session_data)
    if len(sessions) > 50:
        sessions = sessions[-50:]
    if isinstance(change_log, dict):
        change_log["sessions"] = sessions
        change_log["last_updated"] = time.time()
    # 同步到 MySQL（主事实源）
    try:
        version_label = ""
        if isinstance(change_log, dict):
            version_label = change_log.get("version_label", "") or ""
        _default_store().append_change_session(
            session_data, version_label=version_label
        )
    except Exception as _e:
        _log.debug(
            "[figma_node_cache] record_change_session 异常: %s", _e, exc_info=True
        )
    save_change_log(path, change_log)
