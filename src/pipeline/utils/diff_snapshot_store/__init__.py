# -*- coding: utf-8 -*-
"""
版本快照 MySQL 事实源存储 (DiffSnapshotStore)（包入口）
========================================================

定位：把散布在 ``projects/{PROJECT}/智能分析/变更差异/快照/{timestamp}/{snapshot_type}.json``
目录中的版本快照沉淀到 MySQL ``diff_snapshots`` 表，作为 Step1/Step5/文档中心的唯一结构
化事实源。磁盘 JSON 文件降级为显式导入源 / 导出物 / 附件角色，不参与主运行链路兜底。

核心字段：
    - project          项目名
    - version_label    版本标签（如 "20260610_152230"）
    - snapshot_type    快照类型：requirements / api_schema / db_schema / test_cases / cross_dependencies
    - payload_json     快照内容（JSON 文本）
    - item_count       元素个数估计（用于 health_stats）
    - created_at       时间戳

约束：
    - (project, version_label, snapshot_type) 唯一；同版本同类型重复写入走 REPLACE。

【2026-06-18 重构】原 599 行单文件已按职责拆为：
  read.py    — _ReadMixin（load_snapshot / load_previous_version / list_versions / count_snapshots / health_stats）
  write.py   — _WriteMixin（save_snapshot / cleanup_old_versions）
  io_json.py — _IoJsonMixin（显式 JSON 导入/导出，附件角色）
  __init__.py — DiffSnapshotStore facade（基础工具 + 常量）+ 单例
"""

import json
import logging
from typing import Any

from .read import _ReadMixin
from .write import _WriteMixin
from .io_json import _IoJsonMixin

_log = logging.getLogger(__name__)


class DiffSnapshotStore(_ReadMixin, _WriteMixin, _IoJsonMixin):
    """MySQL ``diff_snapshots`` 表读写工具。"""

    KNOWN_TYPES = (
        "requirements",
        "api_schema",
        "db_schema",
        "test_cases",
        "cross_dependencies",
    )

    # 常量：默认保留版本数
    DEFAULT_KEEP_COUNT = 5
    # 常量：错误信息最大截断长度
    ERROR_MSG_TRUNCATE_LEN = 120
    # 常量：版本标签最小有效长度（用于推断时间戳格式）
    VERSION_LABEL_MIN_LEN = 8

    def __init__(self, db_getter=None):
        self._last_error = ""
        self._db_getter = db_getter

    # -----------------------------------------------------------------
    @property
    def last_error(self) -> str:
        return self._last_error

    # -----------------------------------------------------------------
    # DB 连接
    # -----------------------------------------------------------------
    def _get_db(self):
        if self._db_getter:
            return self._db_getter()
        from src.core.db_manager import get_connection

        return get_connection()

    @staticmethod
    def _ensure_tables(c) -> None:
        """占位函数（2026-06-22：diff_snapshots 表已统一由 src/core/schema.sql 负责）。"""
        return

    @staticmethod
    def _to_json(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception as e:
            _log.warning(
                "[DiffSnapshotStore] _to_json 序列化失败，回退为 str: %s",
                str(e)[: DiffSnapshotStore.ERROR_MSG_TRUNCATE_LEN],
            )
            return json.dumps(str(value), ensure_ascii=False)

    @staticmethod
    def _from_json(text: Any, default: Any) -> Any:
        if text is None or text == "":
            return default
        if not isinstance(text, str):
            return text
        try:
            return json.loads(text)
        except Exception as e:
            _log.warning(
                "[DiffSnapshotStore] _from_json 反序列化失败，返回默认值: %s",
                str(e)[: DiffSnapshotStore.ERROR_MSG_TRUNCATE_LEN],
            )
            return default

    @staticmethod
    def _estimate_item_count(data: Any) -> int:
        """根据快照 payload 粗略估算元素数量（用于 health_stats）。"""
        if data is None:
            return 0
        if isinstance(data, (list, tuple)):
            return len(data)
        if isinstance(data, dict):
            # {"requirements": [...], "api_schema": {...}} 场景下尝试取第一数组值
            for v in data.values():
                if isinstance(v, (list, tuple)):
                    return len(v)
            return len(data)
        try:
            return int(data)
        except Exception as e:
            _log.debug(
                "[DiffSnapshotStore] _estimate_item_count 转换失败: %s",
                str(e)[: DiffSnapshotStore.ERROR_MSG_TRUNCATE_LEN],
            )
            return 0


# 单例
diff_snapshot_store = DiffSnapshotStore()
