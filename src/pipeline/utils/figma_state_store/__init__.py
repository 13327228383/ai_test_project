# -*- coding: utf-8 -*-
"""
Figma 运行时状态 MySQL 事实源存储（包入口）
=============================================
把 Step1 figma 拉取链路的三份 JSON 状态文件沉淀到 MySQL 三张表，
作为 Step1 主运行链路的唯一结构化事实源。三份 JSON 文件自身降级为
显式导入源 / 导出物 / 附件角色，不参与 Step1 主运行链路兜底。

三张表：
    - figma_api_quota       API 配额追踪（月度配额 / 调用次数 / 限流状态）
    - figma_change_sessions 变更会话日志（每次拉取的变更摘要，保留最近 50 条）
    - figma_node_hashes     节点级 MD5 哈希字典（变更检测锚点）

典型调用：
    from src.pipeline.utils.figma_state_store import figma_state_store
    # 配额
    figma_state_store.save_quota({"current_month": "2026-06", "total_used": 1})
    quota = figma_state_store.load_quota()
    # 变更会话
    figma_state_store.append_change_session({"changed_nodes": ["A:1"]})
    sessions = figma_state_store.load_change_sessions()
    # 节点哈希
    figma_state_store.save_node_hashes({"A:1": "deadbeef"})
    hashes = figma_state_store.load_node_hashes()
    # 健康检查
    stats = figma_state_store.health_stats()

【2026-06-18 重构】原 570 行单文件已按三张表拆为：
  quota.py        — _QuotaMixin（figma_api_quota 表）
  change_log.py   — _ChangeLogMixin（figma_change_sessions 表）
  node_hashes.py  — _NodeHashesMixin（figma_node_hashes 表）
  __init__.py     — FigmaStateStore 主类（基础工具 + 健康检查） + 单例
"""

import json
import hashlib
import time
from typing import Any

from .quota import _QuotaMixin
from .change_log import _ChangeLogMixin
from .node_hashes import _NodeHashesMixin
import logging

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FigmaStateStore
# ---------------------------------------------------------------------------
class FigmaStateStore(_QuotaMixin, _ChangeLogMixin, _NodeHashesMixin):
    """Figma 配额 / 变更会话 / 节点哈希 的 MySQL 统一存储。

    通过 ``db_getter`` 注入连接后可用于单测；不注入时默认使用
    ``src.core.db_manager.get_connection()``，这与其他 ``*_store.py`` 保持一致。
    """

    _MAX_SESSIONS = 50

    def __init__(self, db_getter=None):
        self._last_error = ""
        self._db_getter = db_getter

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    @property
    def last_error(self) -> str:
        return self._last_error

    def _project(self) -> str:
        try:
            from src.core.config_loader import config

            return getattr(config, "project_name", "") or ""
        except Exception as _e:
            _log.debug("[__init__] _project 异常: %s", _e, exc_info=True)
            return ""

    def _quota_identity(self) -> dict[str, str]:
        """Figma 配额按凭证隔离，不按项目隔离，避免多项目共用同一 Token 时误判额度。"""
        try:
            from src.core.config_loader import config

            token = str(getattr(config, "figma_key", "") or "").strip()
        except Exception as _e:
            _log.debug("[__init__] _quota_identity 异常: %s", _e, exc_info=True)
            token = ""
        if token:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            return {
                "provider": "figma",
                "credential_scope": "token",
                "credential_hash": digest,
            }
        return {
            "provider": "figma",
            "credential_scope": "global",
            "credential_hash": "default",
        }

    def _get_db(self):
        if self._db_getter:
            return self._db_getter()
        try:
            from src.core.db_manager import get_connection

            return get_connection()
        except Exception as e:
            self._last_error = "connect_failed: %s" % str(e)
            raise

    @staticmethod
    def _ensure_tables(c) -> None:
        """占位函数（2026-06-22：figma_api_quota / figma_change_sessions / figma_node_hashes 表已统一由 src/core/schema.sql 负责）。"""
        return

    @staticmethod
    def _to_json(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception as _e:
            _log.debug("[__init__] _to_json 异常: %s", _e, exc_info=True)
            return json.dumps(str(value), ensure_ascii=False)

    @staticmethod
    def _from_json(text: Any, default: Any) -> Any:
        if text is None or text == "":
            return default
        if not isinstance(text, str):
            return text
        try:
            return json.loads(text)
        except Exception as _e:
            _log.debug("[__init__] _from_json 异常: %s", _e, exc_info=True)
            return default

    # ------------------------------------------------------------------
    # 健康统计
    # ------------------------------------------------------------------
    def health_stats(self) -> dict[str, int]:
        return {
            "figma_api_quota": self.count_quota(),
            "figma_change_sessions": self.count_change_sessions(),
            "figma_node_hashes": self.count_node_hashes(),
        }


# ---------------------------------------------------------------------------
# 模块级单例（保持与其他 store 一致）
# ---------------------------------------------------------------------------
figma_state_store = FigmaStateStore()
