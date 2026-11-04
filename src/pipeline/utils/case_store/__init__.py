# -*- coding: utf-8 -*-
"""
测试用例 MySQL 镜像存储
======================

MySQL 是唯一事实源；Excel 仅作为显式导入/导出产物。
本模块负责 test_cases / case_meta 两表读写，并提供 health_stats() 供
一致性健康检查使用。
"""

from ._constants import CASE_EXPORT_COLUMNS
from ._helpers import _to_json, _row_to_intent
from ._store_core import CaseStore

case_store = CaseStore()
