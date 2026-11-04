# -*- coding: utf-8 -*-
"""
project_config/__init__.py — 项目配置路由包入口
==============================================
原 project_config.py 按功能拆分为 fetchers/ 子模块后，本文件保留对外 re-export，
确保已有 import 路径（如 from src.backend.routes.project_config import project_config_bp）
零变更即可继续工作。
"""

from .project_config import (
    project_config_bp,
    ROOT,
    _TYPE_BOOL_KEYS,
    _TYPE_INT_KEYS,
    _TYPE_FLOAT_KEYS,
    _VALID_CONFIG_KEYS,
    _STATIC_CONFIG_KEYS,
    _rebuild_valid_keys_cache,
    _coerce_config_value,
)

__all__ = [
    "project_config_bp",
    "_TYPE_BOOL_KEYS",
    "_TYPE_INT_KEYS",
    "_TYPE_FLOAT_KEYS",
    "_VALID_CONFIG_KEYS",
    "_STATIC_CONFIG_KEYS",
    "_rebuild_valid_keys_cache",
    "_coerce_config_value",
]
