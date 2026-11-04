# -*- coding: utf-8 -*-
"""
文件清理路由 (package)
=====================
/api/clear-files  +  /api/clearable-files  +  /api/clearable-files/children

从原 file_cleanup.py 拆分为 package，通过 _register_routes() 注册到 exec_bp。
外部导入路径不变：from src.backend.routes.file_cleanup import _register_routes
"""

from ._clear import _register_clear_routes
from ._list import _register_list_routes
from ._children import _register_children_routes


def _register_routes():
    _register_clear_routes()
    _register_list_routes()
    _register_children_routes()


# 模块加载时自动注册路由（与原文件行为一致）
_register_routes()
