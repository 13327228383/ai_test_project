from __future__ import annotations

# DatabaseAgentSchemaMixin — Schema 解析与推断

import logging
import re
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


class DatabaseAgentSchemaMixin:
    """Schema 解析与推断方法。"""

    # ---- 从 SQL DDL 动态解析表结构和推断外键 ----
    _TABLE_COLUMNS = {}  # {TABLE: [column_name, ...]} — load_schema 填充
    _TABLE_DETAILS = {}  # ★ P1 新增
    _SEED_DATA = {}  # ★ P1 新增

    # ---- 表模式推断 & 清理顺序自动生成 ----
    _table_patterns = {}  # {"brand_info": "BRAND_INFO", ...} — load_schema 填充
