# -*- coding: utf-8 -*-
"""
docs_analytics/__init__.py — 文档分析路由包入口
=================================================
原 docs_analytics.py 按 facade + fetchers 模式拆分后，
本文件保留对外 re-export，确保已有 import 路径零变更即可继续工作。
"""

from .docs_analytics import docs_analytics_bp, _register_routes
from .fetchers.stats import (
    _build_module_stats,
    _build_executor_stats,
    _build_type_stats,
    _build_risk_level_stats,
    _build_version_stats,
)

__all__ = [
    "docs_analytics_bp",
    "_register_routes",
    "_build_module_stats",
    "_build_executor_stats",
    "_build_type_stats",
    "_build_risk_level_stats",
    "_build_version_stats",
]
