# -*- coding: utf-8 -*-
"""
操作指令库管理 API (Action Library Blueprint)
================================================
提供操作指令库的 CRUD / 搜索 / 持久化。

事实源：MySQL ``action_categories`` + ``action_actions`` 表
        （由 ``src.pipeline.utils.action_library_store.ActionLibraryStore`` 统一封装）

附件：JSON 文件仅用于显式导入 / 导出
     （不再参与主运行链路兜底）
"""

from flask import Blueprint

action_library_bp = Blueprint("action_library", __name__)

# ★ 导入子模块以注册路由（必须在 Blueprint 定义之后）
from src.backend.routes.action_library import routes  # noqa: E402, F811
from ._validators import (
    _validate_action,
    _validate_str_field,
    _check_nesting_depth,
)  # noqa: E402
