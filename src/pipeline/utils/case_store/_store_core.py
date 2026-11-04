# -*- coding: utf-8 -*-
"""
测试用例数据存储核心

本模块是系统的"测试用例数据库访问入口"，职责：
- 测试用例的 CRUD（增删改查）
- 虚拟继承意图（跨版本未变更需求自动继承）
- 执行状态补齐（关联 executions 表）
- Excel 导出与健康统计

【2026-06-18 重构】
原 ~1156 行的单体类已按 CRUD 职责拆分为 crud/ 下的独立 mixin：
- _base.py   — 基础连接、表初始化、虚拟继承读取、执行状态补齐
- _write.py  — upsert / delete / update / clear
- _read.py   — get / list / list_with_meta / get_by_ids / get_by_files
- _filter.py — get_unexecuted / get_failed
- _export.py — export_to_excel / health_stats

CaseStore 继承全部 mixin，对外接口 100% 保持兼容。
"""

from .crud.base import _VisBaseMixin
from .crud.write import _VisWriteMixin
from .crud.read import _VisReadMixin
from .crud.filter import _VisFilterMixin
from .crud.export import _VisExportMixin


class CaseStore(
    _VisBaseMixin,
    _VisWriteMixin,
    _VisReadMixin,
    _VisFilterMixin,
    _VisExportMixin,
):
    """
    测试用例数据存储（门面类）。

    使用方式：
        from src.pipeline.utils.case_store.store_core import CaseStore
        store = CaseStore()
        store.upsert_intents(intents)
        intents = store.list_intents()
    """

    pass


# 模块级单例实例
case_store = CaseStore()
