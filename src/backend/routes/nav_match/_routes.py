# -*- coding: utf-8 -*-
"""导航匹配可观测路由（v5.15, 2026-08-04）——L2 LLM 语义匹配失败率统计 + 人工校准。

暴露 navigation_match_logs 的查询与校准端点：
  GET  /api/nav-match/logs      分页/筛选查询（project/level/calibration）
  GET  /api/nav-match/stats     匹配统计（L1/L2/L3 分布 + LLM 成功率）
  POST /api/nav-match/calibrate 人工校准（确认/纠正某条匹配结果）

通用性：跨系统通用（导航匹配日志与业务字段无关）；只读/校准薄包装。
"""

import logging

from flask import jsonify, request

_log = logging.getLogger(__name__)


def _register_routes():
    """注册导航匹配可观测路由到 exec_bp（与 case_review 同 Blueprint）。"""
    from src.backend.routes.execution import exec_bp
    from src.backend.state import _current_project
    from src.models.permission import check_admin_or_permission
    from src.core.config_loader import get_current_user

    @exec_bp.route("/api/nav-match/logs", methods=["GET"])
    def api_nav_match_logs():
        """查询导航匹配日志（供人工校准界面）。"""
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        from src.pipeline.utils.nav_match_logger import query_logs

        project = _current_project() or ""
        level = request.args.get("level", "")
        calibration = request.args.get("calibration", "")
        limit = int(request.args.get("limit", 100))
        rows = query_logs(
            project=project, level=level, calibration=calibration, limit=limit
        )
        return jsonify({"ok": True, "project": project, "logs": rows})

    @exec_bp.route("/api/nav-match/stats", methods=["GET"])
    def api_nav_match_stats():
        """匹配统计：L1/L2/L3 分布 + LLM 成功率（可观测核心）。"""
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        from src.pipeline.utils.nav_match_logger import stats

        project = _current_project() or ""
        return jsonify({"ok": True, "project": project, "stats": stats(project)})

    @exec_bp.route("/api/nav-match/calibrate", methods=["POST"])
    def api_nav_match_calibrate():
        """人工校准：确认或纠正某条导航匹配结果（写入 corrected_menu 供复盘）。"""
        ok, err = check_admin_or_permission("execution.run", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        body = request.get_json(silent=True) or {}
        log_id = int(body.get("id") or 0)
        corrected_menu = str(body.get("corrected_menu") or "")
        if not log_id:
            return jsonify({"ok": False, "error": "缺少 log id"}), 400
        from src.pipeline.utils.nav_match_logger import calibrate

        user = get_current_user() or ""
        ok2 = calibrate(log_id, corrected_menu, user=user)
        return jsonify({"ok": ok2, "message": "已校准" if ok2 else "校准失败"})
