# -*- coding: utf-8 -*-
"""
AI 健康度仪表盘 API — facade
==============================
提供幻觉率、守卫拦截、校验不一致等指标查询。
数据来源: ai_health_metrics 表（由 metrics_collector 写入）。

【2026-06-18 重构】路由按业务域物理拆分：
- routes_summary.py     — /api/health/summary（最大块）
- routes_trends.py      — /api/health/trends, /api/health/latest-run
- routes_diagnostics.py — /api/health/detailed, /api/health/metrics, /healthz, /readyz
- fetchers/queries.py   — 数据库查询逻辑
- fetchers/format.py    — 格式化与标签构建

本文件仅保留 Blueprint 创建与置信度标签常量（被多个子模块引用）。
"""

from flask import Blueprint
import logging

_log = logging.getLogger(__name__)

# ── 从 fetchers 导入辅助函数（保留对外 re-export 兼容性） ──
from .fetchers.queries import _get_conn, _latest_run_id

health_bp = Blueprint("health", __name__)

# ═══════════════════════════════════════════════════════════════
# 置信度标签映射（静态）
# ═══════════════════════════════════════════════════════════════
ACCURACY_LABELS = {
    "cross_check_api": "high",
    "cross_check_ui": "high",
    "cross_check_enum": "high",
    "cross_check_hallucination_errors": "high",
    "guard_reject": "high",
    "action_attempt": "high",
    "verification_ui_pass": "medium",
    "verification_api_pass": "medium",
    "verification_db_pass": "medium",
    "verification_disagreement": "medium",
    "intent_dominance_dominance_score": "high",
    "intent_dominance_recipe_only_pass": "high",
    "forward_coverage_rate": "medium",
    "orphan_modules": "medium",
    "uncovered_sections": "medium",
    "deep_check_ai_deep_issues": "low",
    "rule_check_rule_warnings": "medium",
}

ACCURACY_LABEL_TEXT = {
    "high": "高",
    "medium": "中",
    "low": "低",
}
