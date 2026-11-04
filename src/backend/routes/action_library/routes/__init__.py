# -*- coding: utf-8 -*-
"""
操作指令库 — 路由子包
"""

# ★ 导入路由子模块以注册路由到 action_library_bp
from src.backend.routes.action_library.routes import _crud  # noqa: E402, F811
from src.backend.routes.action_library.routes import _export  # noqa: E402, F811
