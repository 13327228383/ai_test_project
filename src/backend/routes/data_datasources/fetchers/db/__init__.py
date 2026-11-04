# -*- coding: utf-8 -*-
"""
数据库统计/测试路由处理函数（包入口）。
─────────────────────
re-export api_db_stats, api_db_test
"""

from ._stats import api_db_stats
from ._test import api_db_test

__all__ = ["api_db_stats", "api_db_test"]
