# -*- coding: utf-8 -*-
"""
校准环路由 (facade)
===================
P1-7: 暴露 calibration_samples 统计给前端人审界面。

【职责拆分】
- _routes.py — 路由实现（GET /api/calibration/summary）
- __init__.py — facade：触发自注册到 exec_bp
"""

from ._routes import _register_routes  # noqa: F401

# ★ 模块加载时自动注册路由到 exec_bp
_register_routes()
