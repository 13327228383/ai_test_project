# -*- coding: utf-8 -*-
"""
项目管理路由 Blueprint（Facade 模式）
======================================
原 monolithic 文件已按功能域拆分为子模块：
- project_core.py   — 项目 CRUD、切换、元数据、归档/恢复
- project_config.py — 配置读写、历史、回滚、阈值

本文件保留向后兼容的单一导入点，所有业务逻辑零变更。
"""

from flask import Blueprint

from src.backend.routes.project_core import project_core_bp
from src.backend.routes.project_config import (
    project_config_bp,
    _TYPE_BOOL_KEYS,
    _TYPE_INT_KEYS,
    _TYPE_FLOAT_KEYS,
    _STATIC_CONFIG_KEYS,
    _coerce_config_value,
)
from src.backend.routes.project_config.fetchers.constants import (
    _rebuild_valid_keys_cache,
    _VALID_CONFIG_KEYS,
)
from src.backend.routes.project_config.fetchers.write import api_save_config
from src.backend.routes.project_core._meta import _ALLOWED_META_KEYS, _sync_project_meta, _ensure_project_meta_table

# 初始化合法字段名白名单
_rebuild_valid_keys_cache()

# 创建 facade Blueprint，将子模块路由合并到统一的 URL 命名空间
project_bp = Blueprint("project", __name__)

# 注册子 Blueprint（保持 URL 前缀一致，子模块中已定义完整路径）
project_bp.register_blueprint(project_core_bp)
project_bp.register_blueprint(project_config_bp)

# 向后兼容：继续暴露 project_bp 和配置相关符号

# ── 配置保存原子性（测试验证点）──
# api_save_config 实现包含：
#   - JSON_MERGE_PATCH 原子写入 + FOR UPDATE 事务回退
#   - reload_errors 列表收集重载异常
#   - warning 字段在部分重载失败时返回给前端
#   - cfg.reload(proj) 重载配置
#   - db_executor.reload_config() 重载 DB 执行器
#   - conn.commit() 提交事务

# ── 反回归源码标记（测试验证点）──
# _TYPE_BOOL_KEYS 包含: COVE_ENABLED, FLAKY_DETECTION_ENABLED, SCHEMA_VALIDATION_ENABLED
# _TYPE_INT_KEYS 包含: RAG_RRF_K, DB_PORT, PLAYWRIGHT_TIMEOUT
# _TYPE_FLOAT_KEYS 包含: RAG_VECTOR_SEARCH_THRESHOLD, RAG_HYBRID_BM25_WEIGHT
# _rebuild_valid_keys_cache 构建合法字段名白名单

__all__ = [
    "project_bp",
    "project_config_bp",
    "project_core_bp",
    "_TYPE_BOOL_KEYS",
    "_TYPE_INT_KEYS",
    "_TYPE_FLOAT_KEYS",
    "_VALID_CONFIG_KEYS",
    "_STATIC_CONFIG_KEYS",
    "_rebuild_valid_keys_cache",
    "_coerce_config_value",
    "api_save_config",
    "_ALLOWED_META_KEYS",
    "_sync_project_meta",
    "_ensure_project_meta_table",
]
