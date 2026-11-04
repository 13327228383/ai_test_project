# -*- coding: utf-8 -*-
"""统一 KPI 聚合 API（kpi_bp）—— 流水线强化专项 T5。

★ 端点（url_prefix=/api/kpi，design §7.4 新端点统一约定）：
    - GET /overview?project=&run_id=&version_id=&target=   KPI 总览（一次返回 ≥13 项指标）
    - GET /trend?project=&n=10                             近 N 次运行趋势

★ 响应格式：一律 {"ok": true, ...}；错误 {"ok": false, "error": str}。
★ 权限：只读接口，登录即可（对齐 quality_bp 先例）。
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from src.core.config_loader import get_current_user

_log = logging.getLogger(__name__)

kpi_bp = Blueprint("kpi", __name__, url_prefix="/api/kpi")


@kpi_bp.before_request
def _require_login():
    """KPI 属内部运营数据，未登录一律拒绝。"""
    try:
        if not get_current_user():
            return jsonify({"ok": False, "error": "请先登录"}), 401
    except Exception as e:  # noqa: BLE001 —— 鉴权组件异常时 fail-closed
        _log.warning("[KPI] 登录校验异常(拒绝访问): %s", str(e)[:160])
        return jsonify({"ok": False, "error": "鉴权失败"}), 401
    return None


def _aggregator():
    """延迟导入 KpiAggregator（避免顶层 DB 依赖）。"""
    from src.backend.routes.kpi._aggregator import KpiAggregator

    return KpiAggregator()


@kpi_bp.route("/overview", methods=["GET"])
def api_kpi_overview():
    """KPI 总览：单次聚合全部指标 + 分布 + 趋势 + 台账不符明细。

    Query:
        project   : str  可选，缺省取当前项目（_resolve_project）
        run_id    : str  可选，缺省 = 最近一次运行
        version_id: str  可选
        target    : str  可选，台账靶子 key（空 = 聚合全部靶子台账）
    """
    from src.backend.state import _resolve_project

    project = _resolve_project(request.args.get("project"))
    run_id = (request.args.get("run_id") or "").strip()
    version_id = (request.args.get("version_id") or "").strip()
    target = (request.args.get("target") or "").strip()

    if not project:
        return jsonify({"ok": False, "error": "缺少 project 参数"}), 400

    try:
        result = _aggregator().aggregate(
            project=project, run_id=run_id, version_id=version_id, target=target
        )
        return jsonify(result)
    except Exception as e:  # noqa: BLE001 —— 聚合异常整体降级
        _log.error("[KPI] overview 聚合异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": f"KPI 聚合失败: {e}"}), 500


@kpi_bp.route("/trend", methods=["GET"])
def api_kpi_trend():
    """近 N 次运行趋势（KPI 趋势折线图数据源）。"""
    from src.backend.state import _resolve_project

    project = _resolve_project(request.args.get("project"))
    try:
        n = int(request.args.get("n", 10))
    except (TypeError, ValueError):
        n = 10
    if not project:
        return jsonify({"ok": False, "error": "缺少 project 参数"}), 400
    trend = _aggregator().trend(project, n=n)
    return jsonify({"ok": True, "project": project, "trend": trend})
