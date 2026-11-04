# -*- coding: utf-8 -*-
"""
视觉 Oracle 路由 (facade)
=========================
P1-7: 暴露 VLMOracle.check() 给前端人审界面。

【职责拆分】
- _routes.py — 路由实现（POST /api/visual-review, GET /api/visual-review/status）
- __init__.py — facade：触发自注册到 exec_bp
"""

from ._routes import _register_routes  # noqa: F401

# ★ 模块加载时自动注册路由到 exec_bp
_register_routes()
