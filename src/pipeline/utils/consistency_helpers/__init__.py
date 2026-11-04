# -*- coding: utf-8 -*-
"""
流水线一致性检查器 — 辅助函数与子检查
======================================

从 consistency_checker.py 中提取的可复用工具函数、store 加载辅助、
子检查逻辑和修复操作，供主模块导入使用。

本模块为 facade，所有符号从子模块重新导出，保持向后兼容。
"""

from .base import (  # noqa: F401
    Issue,
    _count_severity,
    _extract_case_ids,
    _extract_req_ids,
    _find_duplicate_case_ids,
    _get_explicit_intent_req_id,
    _infer_req_id_from_case_id,
    _is_true,
    _load_mapping,
    _make_issue,
    _parse_steps,
    _safe_get_case_id,
    _safe_get_req_id,
)
from .cli import (  # noqa: F401
    _handle_cli_export,
    _handle_cli_health,
    _handle_cli_repair_output,
    _handle_cli_reset,
    _handle_cli_sync,
)
from .mysql import (  # noqa: F401
    _check_mysql_mapping_consistency,
    _check_mysql_store_tables,
    _check_mysql_trace_consistency,
    _check_store_empty,
)
from .print import (  # noqa: F401
    _format_items_preview,
    _print_empty_table_hints,
    print_mysql_health_report,
)
from .repair import (  # noqa: F401
    apply_safe_missing_mapping_repair,
    apply_stale_mapping_repair,
    reset_user_db,
    sync_excel_to_mysql,
)
from .store import (  # noqa: F401
    _try_health_stats,
    _try_load_store_data,
)
