# -*- coding: utf-8 -*-
"""
AI 模型选择路由 (package facade)
================
外部导入保持不变:
    from src.backend.routes.data_models import data_models_bp
"""

from flask import Blueprint

data_models_bp = Blueprint("data_models", __name__)

from ._list import _register_list_routes
from ._detail import _register_detail_routes
from ._create import _register_create_routes
from ._delete import _register_delete_routes

_register_list_routes()
_register_detail_routes()
_register_create_routes()
_register_delete_routes()
