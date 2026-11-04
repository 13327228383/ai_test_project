# -*- coding: utf-8 -*-
"""
执行控制路由 (facade)
=====================
从 run.py 拆分为 package 模式。
包含：coverage-check, run-step, step4-run, run-all, stop, stop-run, pause, resume, retry-failed

★ 修复 2026-06-22: 不再创建独立的 run_bp（它从未被 app.py 注册），
   而是将所有路由直接注册到上级 exec_bp 上 — 与 review/config/preflight 子模块行为一致。
"""

from src.backend.routes.execution import exec_bp

from ._start import _register_start_routes
from ._status import _register_status_routes
from ._stop import _register_stop_routes

# 直接挂到 exec_bp（app.py 注册的 Blueprint），避免子 Blueprint 漏注册
_register_start_routes(exec_bp)
_register_status_routes(exec_bp)
_register_stop_routes(exec_bp)
