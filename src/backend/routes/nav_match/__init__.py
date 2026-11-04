# -*- coding: utf-8 -*-
"""
导航匹配可观测路由 (facade)
============================
v5.15: 暴露 navigation_match_logs 查询/统计/校准给前端人审界面。

【职责拆分】
- _routes.py — 路由实现（GET /api/nav-match/logs, /stats, POST /calibrate）
- __init__.py — facade：触发自注册到 exec_bp
"""

from ._routes import _register_routes  # noqa: F401

# ★ 模块加载时自动注册路由到 exec_bp
_register_routes()
