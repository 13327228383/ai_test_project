# -*- coding: utf-8 -*-
"""
测试用例表 CRUD 工具函数 (Package Facade)
==========================================
从 ExcelHandler 类中提取的测试用例表操作方法，转为独立函数。
所有方法通过 case_store / review_store 等操作 MySQL。

拆分后的子模块：
- _write.py : 写入操作
- _read.py  : 读取操作
- _update.py: 更新操作
- _delete.py: 删除操作

★ 修复（2026-06-23）：补全所有公开函数的 re-export（原仅部分导出，导致
   `from src.core.excel_cases import write_case_meta / read_test_cases_with_meta
   / get_intents_by_ids` 等多处 ImportError，Step3 并行建模异常中断）。
"""

from ._write import (
    write_test_cases,
    init_test_cases_file,
    append_intents,
    parse_and_write_intents,
    write_case_meta,
)
from ._read import (
    read_test_cases,
    read_test_cases_with_meta,
    get_intents_by_ids,
    get_intents_by_indices,
)
from ._update import (
    update_intent_status,
    update_test_case,
    update_verification_status,
)
from ._delete import (
    delete_intent,
    delete_test_case,
)

__all__ = [
    # write
    "write_test_cases",
    "init_test_cases_file",
    "append_intents",
    "parse_and_write_intents",
    "write_case_meta",
    # read
    "read_test_cases",
    "read_test_cases_with_meta",
    "get_intents_by_ids",
    "get_intents_by_indices",
    # update
    "update_intent_status",
    "update_test_case",
    "update_verification_status",
    # delete
    "delete_intent",
    "delete_test_case",
]
