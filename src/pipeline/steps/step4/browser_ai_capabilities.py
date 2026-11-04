# -*- coding: utf-8 -*-
"""
browser_ai 标准能力 facade（从 capabilities 子包重新导出）
==========================================================
因原文件 674 行过大，已按函数分组拆分为 capabilities/ 子模块：

- capabilities/_search.py  → try_search_or_filter
- capabilities/_delete.py  → try_delete_record
- capabilities/_export.py  → try_export_data
- capabilities/_create.py  → try_create_business_record

本文件保持对外接口完全兼容，所有外部 import 无需修改。
"""

from src.pipeline.steps.step4.capabilities import (
    try_search_or_filter,
    try_delete_record,
    try_export_data,
    try_create_business_record,
    try_login_with_password,
    try_form_fill_and_submit,
    try_data_query,
    try_approval_flow,
    try_list_batch_action,
    try_navigate_menu,
    try_pagination_control,
    try_table_export,
)

__all__ = [
    "try_search_or_filter",
    "try_delete_record",
    "try_export_data",
    "try_create_business_record",
    "try_login_with_password",
    "try_form_fill_and_submit",
    "try_data_query",
    "try_approval_flow",
    "try_list_batch_action",
    "try_navigate_menu",
    "try_pagination_control",
    "try_table_export",
]
