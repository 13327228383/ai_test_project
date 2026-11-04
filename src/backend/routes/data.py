# -*- coding: utf-8 -*-
"""
data.py — 数据路由统一入口（facade）
====================================
【2026-06-18 拆分】路由按业务域物理拆分：
- _data_helpers.py   — _check_login_and_project, _current_project
- routes/_plugin.py  — /api/plugin-metrics
- routes/_token.py   — /api/token-stats, /api/token-dashboard
- routes/_misc.py    — /api/stats, /api/figma, /api/intent-metrics, /api/tool-mode-metrics

本文件仅保留 Blueprint 创建、辅助函数 re-export 和子模块路由注册。
外部导入路径不变：from src.backend.routes.data import data_bp
"""

from flask import Blueprint
import logging

_log = logging.getLogger(__name__)

data_bp = Blueprint("data", __name__)

# ═══════════════════════════════════════════════════════════════
# 子模块路由注册（按原 data.py 中的路由顺序）
# ═══════════════════════════════════════════════════════════════
from src.backend.routes.routes._plugin import (
    register as _reg_plugin,
)  # noqa: E402, F811
from src.backend.routes.routes._misc import register as _reg_misc  # noqa: E402, F811
from src.backend.routes.routes._token import register as _reg_token  # noqa: E402, F811

_reg_plugin(data_bp)  # /api/plugin-metrics
_reg_misc(
    data_bp
)  # /api/stats, /api/figma, /api/intent-metrics, /api/tool-mode-metrics
_reg_token(data_bp)  # /api/token-stats, /api/token-dashboard
