# -*- coding: utf-8 -*-
"""
数据源 & 需求审核 & 背景配置路由 — Facade
============================================
作为 data_datasources 包的入口，定义常量，
并从 fetchers 子模块导入路由处理函数，完成路由注册。

[B3.3 收敛] ``_check_login_and_project()`` 原在本文件内联定义，现已下线，
统一改从唯一真源 ``src.backend.routes._data_helpers`` 导入。
实测三份副本（真源 / 本文件 / ``data_models/_list.py``）均使用
``state._current_project``，**逻辑逐字等价**，故本次去重为零行为变更
（设计文档中"另两处路由权限会收紧"的判断与实测不符，已在交付说明中更正）。

[已复核-DeepSeek-V3 2026-08-16]

包含路由:
/api/requirements  /api/requirements/review  /api/requirements/review/confirm
/api/upload-bg  /api/bg-image  /api/bg-config
/api/figma-quota  /api/figma-refresh
/api/yapi-stats  /api/yapi-refresh
/api/db-stats  /api/db-test
"""

import time
import logging
from functools import partial

# 注：原 ``from flask import jsonify`` 已随 B3.3 收敛删除 —— 唯一使用它的
# 内联 ``_check_login_and_project()`` 已下线，改从 ``_data_helpers`` 导入。

_log = logging.getLogger(__name__)

# ── 阈值常量（公共导出）──
_BG_UPLOAD_MAX_SIZE = 10 * 1024 * 1024  # 10MB
_DB_STATS_ERROR_COOLDOWN = 60  # 同类错误降噪间隔（秒）
_DB_CONNECT_TIMEOUT = 5  # 数据库连接超时（秒）


def _register_routes():
    """注册所有数据源相关路由到 data_bp。"""
    from src.backend.routes.data import data_bp
    from src.models.permission import check_admin_or_permission

    # ★ [B3.3 收敛] 登录+项目权限校验统一走唯一真源 _data_helpers。
    #   原本此处有一份内联同名副本，与真源**逐行等价**（同样取 state._current_project），
    #   故本次去重**不产生任何行为变化**，仅消除三副本各自漂移的风险。
    from src.backend.routes._data_helpers import _check_login_and_project

    # ── 导入 fetchers 路由处理函数 ──
    from .fetchers.requirements import (
        api_requirements,
        api_review_requirement,
        api_confirm_all_requirements,
    )
    from .fetchers.bg import (
        api_upload_bg,
        api_bg_image,
        api_bg_image_by_id,
        api_bg_gallery_list,
        api_bg_gallery_delete,
        api_bg_gallery_activate,
        api_bg_config,
    )
    from .fetchers.input_style import api_input_style
    from .fetchers.figma import (
        api_figma_quota,
        api_figma_refresh,
        api_figma_retry_failed,
        api_figma_images,
        api_figma_image_data,
    )
    from .fetchers.yapi import (
        api_yapi_stats,
        api_yapi_refresh,
    )
    from .fetchers.confluence import (
        api_confluence_stats,
        api_confluence_refresh,
    )
    from .fetchers.db import (
        api_db_stats,
        api_db_test,
    )

    # ── 注册路由 ──
    # 需求审核（无额外参数依赖）
    data_bp.route("/api/requirements")(api_requirements)
    data_bp.route("/api/requirements/review", methods=["POST"])(api_review_requirement)
    data_bp.route("/api/requirements/review/confirm", methods=["POST"])(
        api_confirm_all_requirements
    )

    # 背景配置（api_upload_bg 需要 _BG_UPLOAD_MAX_SIZE）
    _upload_bg = partial(api_upload_bg, _BG_UPLOAD_MAX_SIZE=_BG_UPLOAD_MAX_SIZE)
    _upload_bg.__name__ = api_upload_bg.__name__
    data_bp.route("/api/upload-bg", methods=["POST"])(_upload_bg)
    data_bp.route("/api/bg-image")(api_bg_image)
    data_bp.route("/api/bg-image/<gid>")(api_bg_image_by_id)
    data_bp.route("/api/bg-gallery")(api_bg_gallery_list)
    data_bp.route("/api/bg-gallery/<gid>", methods=["DELETE"])(api_bg_gallery_delete)
    data_bp.route("/api/bg-gallery/activate", methods=["POST"])(api_bg_gallery_activate)
    data_bp.route("/api/bg-config", methods=["GET", "POST"])(api_bg_config)
    data_bp.route("/api/input-style", methods=["GET", "POST"])(api_input_style)

    # Figma（无额外参数依赖）
    data_bp.route("/api/figma-quota")(api_figma_quota)
    data_bp.route("/api/figma-refresh", methods=["POST"])(api_figma_refresh)
    data_bp.route("/api/figma-retry-failed", methods=["POST"])(api_figma_retry_failed)
    data_bp.route("/api/figma-images")(api_figma_images)
    data_bp.route("/api/figma-image/<image_id>")(api_figma_image_data)

    # YApi（api_yapi_stats 需要 _check_login_and_project）
    _yapi_stats = partial(
        api_yapi_stats, _check_login_and_project=_check_login_and_project
    )
    _yapi_stats.__name__ = api_yapi_stats.__name__
    data_bp.route("/api/yapi-stats")(_yapi_stats)
    data_bp.route("/api/yapi-refresh", methods=["POST"])(api_yapi_refresh)

    # Confluence（api_confluence_stats 需要 _check_login_and_project）
    _confluence_stats = partial(
        api_confluence_stats, _check_login_and_project=_check_login_and_project
    )
    _confluence_stats.__name__ = api_confluence_stats.__name__
    data_bp.route("/api/confluence-stats")(_confluence_stats)
    data_bp.route("/api/confluence-refresh", methods=["POST"])(api_confluence_refresh)

    # 数据库（api_db_stats 需要 _check_login_and_project + _DB_STATS_ERROR_COOLDOWN）
    _db_stats = partial(
        api_db_stats,
        _check_login_and_project=_check_login_and_project,
        _DB_STATS_ERROR_COOLDOWN=_DB_STATS_ERROR_COOLDOWN,
    )
    _db_stats.__name__ = api_db_stats.__name__
    data_bp.route("/api/db-stats")(_db_stats)
    data_bp.route("/api/db-test", methods=["POST"])(api_db_test)

    _log.info("[data_datasources] 路由已注册（facade + fetchers 模式）")


# 模块加载时自动注册路由
_register_routes()
