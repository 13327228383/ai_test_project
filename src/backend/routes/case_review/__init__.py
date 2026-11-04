# -*- coding: utf-8 -*-
"""
执行控制 — 测试用例审核端点 (facade)
========================
Step 3 完成后触发测试用例审核闸门（人工确认/编辑/删除/重新建模）。

【2026-06-18 重构】按职责拆分为：
- _state.py   — 审核状态管理（常量 / 存储 / 读取）
- _routes.py  — 审核路由（所有 @exec_bp.route 端点 + 辅助函数）
- _remodel.py — AI 重新建模逻辑
- __init__.py — facade：创建 Blueprint + re-export + 触发自注册
"""

from flask import Blueprint

case_review_bp = Blueprint("case_review", __name__)

# ── 从拆分子模块 re-export（保持外部导入路径不变） ──
from ._state import (  # noqa: F401,E402
    _CONFIDENCE_HIGH,
    _CONFIDENCE_LOW,
    _APPROVAL_RATE_THRESHOLD,
    _get_case_review_state,
    _read_intents_for_review,
)
from ._routes import (  # noqa: F401,E402
    _log_review,
    _register_routes,
)

# ★ 模块加载时自动注册路由到 exec_bp
_register_routes()
