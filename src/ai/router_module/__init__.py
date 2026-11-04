# -*- coding: utf-8 -*-
"""
路由模块 (RouterModule) — facade
=================================
外部导入路径不变：from src.ai.router_module import RouterModule, router
"""

# ── 从拆分子模块 re-export（维持原 API） ──
from .task_types import TaskType, _MODEL_PRICING
from .local_ollama import call_local_model, validate_json_output
from ._router import RouterModule, router, _log  # _log 供 observers 日志劫持
