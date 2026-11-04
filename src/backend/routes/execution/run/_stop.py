# -*- coding: utf-8 -*-
"""
停止路由
========
stop, stop-run
"""

import logging
from src.core.guarded import guard
from flask import jsonify

_log = logging.getLogger(__name__)

from src.backend.routes.execution import (
    _current_project,
    _active_runs,
    _active_runs_lock,
    _set_force_stop,
    _set_paused_step,
    _get_paused_step,
)


def _register_stop_routes(run_bp):
    """将停止类路由注册到 run_bp Blueprint。"""

    def _force_cleanup_after_timeout(active_ids, timeout=15, project=None):
        """后台 watchdog：超时后强制清理卡死的 _active_runs 记录。

        ★ 通用修复(2026-07-21 v12): 同步清除 _force_stop_by_project 标记。
          根因(3层追问):
            1. 表层: stop 后启动新 run，新 run 立即被 "流水线已被用户强制终止" 终止
            2. 中层: scheduler._force_stop_check 检查 _get_force_stop(project) 返回 True
            3. 根因: stop 设置 _force_stop=True 后，watchdog 只清理 _active_runs
                     但不清除 _force_stop，导致标记残留，新 run 一启动就被判定为"被强制停止"
          通用性:
            - 跨系统通用: 不依赖具体业务，纯状态清理
            - 跨场景通用: 无论 run 是否正常进入 finally，watchdog 都会兜底清除标记
            - 覆盖性: 此处清除是 finally 块(_run_step.py L419)之外的兜底，双重保险
        """
        import time as _t

        _t.sleep(timeout)
        with guard("_force_cleanup_after_timeout: with _active_runs_lock: for rid in a", log=_log, level=logging.WARNING):
            with _active_runs_lock:
                for rid in active_ids:
                    if rid in _active_runs:
                        _log.warning(
                            "[STOP WATCHDOG] 任务 %s 在 %ds 后仍未结束，强制清理",
                            rid,
                            timeout,
                        )
                        with guard("_force_cleanup_after_timeout: _active_runs.pop(rid, None)", log=_log, level=logging.WARNING):
                            _active_runs.pop(rid, None)
            # ★ 通用修复(v12): 同步清除 force_stop 标记，防止残留导致新 run 被误终止
            if project:
                with guard("_force_cleanup_after_timeout: _set_force_stop(False, project)", log=_log, level=logging.WARNING):
                    _set_force_stop(False, project)
                    _log.info(
                        "[STOP WATCHDOG] 已清除 project=%s 的 force_stop 标记", project
                    )
            with guard("_force_cleanup_after_timeout: from src.backend.state import _set_r", log=_log, level=logging.WARNING):
                from src.backend.state import _set_running

                _set_running(False)

    @run_bp.route("/api/stop", methods=["POST"])
    def api_stop():
        from src.models.permission import can_execute_in_project

        _cur_project = _current_project()
        ok, err = can_execute_in_project(None, _cur_project)
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        import src.backend.state as _st

        with _active_runs_lock:
            # ★ A1 改造：停止操作仅影响当前项目的任务，不误杀其他项目
            active_ids = [
                rid
                for rid, r in _active_runs.items()
                if r.get("project") == _cur_project
            ]
            if not active_ids and _get_paused_step(_cur_project) <= 0:
                return jsonify({"ok": False, "error": "没有运行中的任务"}), 400
            _set_force_stop(True, _cur_project)
            _set_paused_step(0, _cur_project)
            _st._pause_requested_by_project.pop(_cur_project, None)
            _st._thinking_active = False
            _st._clear_thinking(_cur_project)  # ★ A1: 清除 per-project
            for rid in active_ids:
                if rid in _active_runs:
                    _active_runs[rid]["force_stop"] = True
        with guard("api_stop: from src.pipeline.utils.pause_contro", log=_log, level=logging.WARNING):
            from src.pipeline.utils.pause_controller import pause_controller

            # ★ A2 改造：停止时恢复当前项目的暂停（非全局 resume_all，避免误唤醒其他项目）
            if pause_controller.for_project(_cur_project).is_paused:
                pause_controller.for_project(_cur_project).resume()
        # ★ 启动 watchdog：15 秒后强制清理仍在 _active_runs 中的卡死记录
        # ★ v12: 传入 project 以便 watchdog 同步清除 force_stop 标记
        import threading

        threading.Thread(
            target=_force_cleanup_after_timeout,
            args=(active_ids, 15, _cur_project),
            daemon=True,
        ).start()
        _log.info(
            "##HTML:##<span style='color:#ef4444;font-weight:bold'>⏹ [SYSTEM] 用户终止 %s 项目任务 (%d 个)</span>",
            _cur_project,
            len(active_ids),
        )
        _log.info(
            "##HTML:##<span style='color:#94A3B8'>  ⓘ 停止信号已发送，当前正在执行的步骤完成后即停止（AI 调用无法中途打断）</span>"
        )
        return jsonify(
            {
                "ok": True,
                "message": f"已终止 {len(active_ids)} 个任务",
                "force_cleanup_in": 15,
            }
        )

    @run_bp.route("/api/stop/<run_id>", methods=["POST"])
    def api_stop_run(run_id):
        from src.models.permission import check_admin_or_permission

        ok, err = check_admin_or_permission(
            "execution.stop", project=_current_project()
        )
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        with _active_runs_lock:
            if run_id not in _active_runs:
                return jsonify({"ok": False, "error": "任务不存在或已结束"}), 404
            _active_runs[run_id]["force_stop"] = True
        _log.info(
            f"##HTML:##<span style='color:#ef4444;font-weight:bold'>⏹ [SYSTEM] 用户终止任务 {run_id}</span>"
        )
        return jsonify({"ok": True, "message": f"已终止 {run_id}"})
