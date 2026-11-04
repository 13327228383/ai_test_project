# -*- coding: utf-8 -*-
"""
追溯链路 MySQL 事实源存储
=========================

定位：把原有的 ``trace_links.json`` 沉淀到 MySQL ``trace_links`` 表，
作为 Step3/Step5/审核链路的唯一结构化事实源。``trace_links.json`` 自身降级为
显式导入源/导出物/附件角色，不再参与主运行链路兜底。

核心字段：
    - trace_links 表（需求级元数据）
        - req_id / project / version_id  主键
        - module / description  基础描述
        - related_reqs_json     关联需求JSON
        - confidence            置信度（high / medium / low）
        - payload_json          链路元数据快照（不含API/DB/Figma资源详情）
        - created_at / updated_at
    - trace_link_chunks 表（单一事实源：资源关联）
        - req_id / project / version_id / chunk_type / chunk_id  主键
        - chunk_type: api / db_table / figma_page / dom / requirement / upload_*
        - chunk_id: 指向 rag_chunks.chunk_id
        - rel_type / confidence / created_at

【2026-06-18 重构】原 426 行单文件按职责拆分：
- crud.py        — _CrudMixin（upsert_links / clear_project_links）
- read_health.py — _ReadHealthMixin（load_links / count / health_stats）
- io_export.py   — _IOExportMixin（export_to_json / import_from_json / export_matrix_markdown）
- __init__.py    — facade TraceLinkStore + 单例 + 共享 DB 连接 + JSON 编解码
"""

import json
from typing import Any
import logging

_log = logging.getLogger(__name__)

from .crud import _CrudMixin
from .read_health import _ReadHealthMixin
from .io_export import _IOExportMixin


class TraceLinkStore(_CrudMixin, _ReadHealthMixin, _IOExportMixin):
    """MySQL ``trace_links`` 表读写工具。"""

    def __init__(self, db_getter=None):
        self._last_error = ""
        self._db_getter = db_getter

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
        """占位函数（2026-06-22：trace_links 表已统一由 src/core/schema.sql 负责）。"""
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


trace_link_store = TraceLinkStore()
