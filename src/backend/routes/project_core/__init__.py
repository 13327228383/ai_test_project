# -*- coding: utf-8 -*-
"""
项目核心路由 —— 项目管理、切换、元数据、归档/恢复
Package facade：保持外部导入路径不变
"""

from flask import Blueprint

project_core_bp = Blueprint("project_core", __name__)

from ._meta import _register_meta_routes
from ._project import _register_project_routes
from ._archive import _register_archive_routes

_register_meta_routes()
_register_project_routes()
_register_archive_routes()
