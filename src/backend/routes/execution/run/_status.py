# -*- coding: utf-8 -*-
"""
状态路由
========
pause, resume
"""

import logging
from src.core.guarded import guard
from flask import jsonify

_log = logging.getLogger(__name__)

from src.backend.routes.execution import (
    _is_running,
    _get_paused_step,
    _set_paused_step,
)


def _register_status_routes(run_bp):
    """将状态类路由注册到 run_bp Blueprint。"""

    @run_bp.route("/api/pause", methods=["POST"])
    def api_pause():
        from src.models.permission import check_admin_or_permission
        from src.backend.state import _current_project

        _cur_proj = _current_project()
        ok, err = check_admin_or_permission("execution.run", project=_cur_proj)
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        # ★ A1 改造：按 project 检查运行/暂停状态
        if not _is_running(_cur_proj) or _get_paused_step(_cur_proj) > 0:
            return jsonify({"ok": False, "error": "无法暂停"}), 400
        with guard("api_pause: from src.pipeline.utils.pause_contro", log=_log, level=logging.WARNING):
            from src.pipeline.utils.pause_controller import pause_controller

            pause_controller.pause()
        _log.info(
            "##HTML:##<span style='color:#f59e0b;font-weight:bold'>⏸ 用户点击暂停，等待当前操作完成后停止...</span>"
        )
        return jsonify({"ok": True, "message": "暂停中"})

    @run_bp.route("/api/resume", methods=["POST"])
    def api_resume():
        from src.models.permission import check_admin_or_permission
        from src.backend.state import _current_project

        _cur_proj = _current_project()
        ok, err = check_admin_or_permission("execution.run", project=_cur_proj)
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        from src.pipeline.utils.pause_controller import pause_controller

        # ★ A1 改造：按 project 检查暂停状态
        if not pause_controller.is_paused and _get_paused_step(_cur_proj) <= 0:
            return jsonify({"ok": False, "error": "没有暂停的任务"}), 400
        if pause_controller.is_paused:
            with guard("api_resume: pause_controller.resume()", log=_log, level=logging.WARNING):
                pause_controller.resume()
        _set_paused_step(0, _cur_proj)
        _log.info(
            f"##HTML:##<span style='color:#22c55e;font-weight:bold'>▶ 继续执行...</span>"
        )
        return jsonify({"ok": True, "message": "继续执行"})
