# -*- coding: utf-8 -*-
"""
docs_analytics.py — 文档分析路由（facade）
==========================================

【2026-06-18 重构】
原单体文件按 facade + fetchers + routes 模式拆分：
- fetchers/_stats.py   — 统计数据生成
- fetchers/_search.py  — 文件扫描/搜索/路径/版本辅助
- routes/_file_tree.py — 文件树 + 文件列表 API
- routes/_preview.py   — 文件预览 API（含 XMind 解析）
- routes/_save.py      — 文件保存 + 文件下载 API
- routes/_search.py    — 文件搜索 API
- routes/_storage.py   — 存储用量统计 API

本文件保留 Blueprint 创建、常量、以及 _register_routes() 路由注册函数。
"""

import logging
from flask import Blueprint

_log = logging.getLogger(__name__)

from src.backend.state import _current_project

# ── 阈值常量 ──
_SEARCH_MIN_QUERY_LEN = 2
_SEARCH_DEFAULT_LIMIT = 50

# 创建 Blueprint，在 app.py 中注册
docs_analytics_bp = Blueprint("docs_analytics", __name__)

# ============================================================================
# 从 fetchers 导入辅助函数
# ============================================================================

from .fetchers.stats import (
    _build_module_stats,
    _build_executor_stats,
    _build_type_stats,
    _build_risk_level_stats,
    _build_version_stats,
)
from .fetchers.search import (
    _scan_dir,
    _safe_path,
    _infer_doc_type,
    _auto_version_mysql,
    _FILE_ICONS,
    _PREVIEWABLE,
    _EDITABLE,
)


# ═══════════════════════════════════════════════════════════════
# 路由注册 — 汇聚各 routes 子模块
# ═══════════════════════════════════════════════════════════════


def _register_routes():
    from .routes import _file_tree, _preview, _save, _search, _storage

    _file_tree.register(docs_analytics_bp)
    _preview.register(docs_analytics_bp)
    _save.register(docs_analytics_bp)
    _search.register(docs_analytics_bp)
    _storage.register(docs_analytics_bp)


# 模块加载时自动注册路由
_register_routes()
