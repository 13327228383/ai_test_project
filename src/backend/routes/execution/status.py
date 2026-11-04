# -*- coding: utf-8 -*-
"""
Status 路由
============
从 execution.py 拆分，注册到 exec_bp Blueprint。
包含：api_status, api_version_check_confirm, api_version_check_cancel
"""

import time
from src.core.guarded import guard
import logging
from flask import jsonify

_log = logging.getLogger(__name__)

from src.backend.routes.execution import (
    exec_bp,
    _active_runs,
    _active_runs_lock,
    _get_paused_step,
    _get_version_warning,
)


def _get_case_review_state_lazy():
    """惰性获取测试用例审核状态，避免循环导入。"""
    try:
        from src.backend.routes.case_review import _get_case_review_state

        return _get_case_review_state()
    except Exception as e:
        _log.warning("[status] 获取测试用例审核状态失败: %s", e)
        return {}


@exec_bp.route("/api/status")
def api_status():
    try:
        import src.backend.state as _st
        from flask import session as _flask_session
        from src.core.config_loader import get_current_user, set_current_user

        # /api/status 是白名单接口，会绕过 app.before_request 的 session -> thread-local 同步。
        # 已登录用户轮询时必须在这里恢复，否则前端看不到 running/active_runs。
        uid = _flask_session.get("user_id") or get_current_user()
        if uid:
            set_current_user(uid)

        # ★ 未登录用户：返回最小状态信息（200），不泄漏敏感字段
        # /api/status 是公开端点（已在 _middleware._PUBLIC_PATHS 白名单），
        # 未登录时只返回 ok/version，不返回 running/paused/has_key/is_admin
        # /provider/key_mode/user/active_runs 等敏感字段。
        if not uid:
            return jsonify({"ok": True, "version": "1.0.0"}), 200

        # ── 已登录用户：返回完整状态 ──
        # ★ A1 改造：paused 按 project 隔离（向后兼容：无 project 时返回全局任一暂停）
        _current_proj_for_pause = _st._current_project()
        paused = _get_paused_step(_current_proj_for_pause) > 0
        _thinking = _st._get_thinking(_current_proj_for_pause)
        elapsed = (
            time.time() - _thinking.get("start_ts", 0) if _thinking.get("active") else 0
        )
        # ★ 原子读取 active_runs + 清理僵尸线程（thread 已死但条目残留）
        #   存活但长时间无进展的 run 由 sweep 置 force_stop（判据=进展停滞，非总时长）。
        _now = time.time()
        with _active_runs_lock:
            from src.backend.state import _sweep_stale_runs

            _sweep_stale_runs()
            _current_proj = _st._current_project()
            running = any(
                True
                for r in _active_runs.values()
                if not _current_proj
                or not r.get("project")
                or r.get("project") == _current_proj
            )
            # ★ 修复：从 _run_progress + checkpoint_store 读取实时步骤状态，
            # 不再使用 _active_runs 中创建时写死的 step 字段（多步执行时永不更新）
            # ★ 按项目过滤：只返回当前项目的 active runs，避免串扰
            active_runs = []
            for rid, r in _active_runs.items():
                # 只保留当前项目的 active runs
                if (
                    _current_proj
                    and r.get("project")
                    and r.get("project") != _current_proj
                ):
                    continue
                run_info = {
                    "run_id": rid,
                    "step": r["step"],
                    "project": r["project"],
                    "start_ts": r["start_ts"],
                }
                # 优先从 _run_progress 读取当前步骤（scheduler 每步开始时更新）
                with _st._run_progress_lock:
                    prog = _st._run_progress.get(rid, {})
                if prog:
                    run_info["current_step"] = prog.get("current_step", r["step"])
                    run_info["end_step"] = prog.get("to_step", r["step"])
                    run_info["step_label"] = prog.get("step_label", "")
                # 从 checkpoint_store 读取各步骤完成状态
                with guard("api_status: from src.pipeline.utils.checkpoint_s", log=_log):
                    from src.pipeline.utils.checkpoint_store import checkpoint_store

                    cps = checkpoint_store.get_checkpoints(rid)
                    step_statuses = {}
                    for cp in cps:
                        step_statuses[str(cp["step"])] = cp[
                            "status"
                        ]  # running/success/failed
                    run_info["step_statuses"] = step_statuses
                active_runs.append(run_info)
        if not running and not paused:
            _st._thinking_active = False
            _st._clear_thinking(_current_proj_for_pause)  # ★ A1: 清除 per-project
        with _st._run_progress_lock:
            progress = dict(_st._run_progress)
        # ★ Key 状态（供前端判断是否需要弹出设置面板）
        from src.core.config_loader import (
            get_session_key,
            get_session_provider,
            set_session_key,
        )

        session_key = get_session_key() or ""
        # ★ 兜底：如果 session key 丢失（如服务器重启），从用户 DB 自动恢复
        if not session_key:
            if uid:
                with guard("api_status: from src.users.manager import get_us", log=_log, level=logging.WARNING):
                    from src.users.manager import get_user_credential

                    persist_key = get_user_credential(uid, "API_KEY", project="*")
                    if persist_key:
                        set_session_key(persist_key)
                        session_key = persist_key
        has_key = bool(session_key)
        provider = get_session_provider() if has_key else ""
        key_mode = (
            "local"
            if (has_key and session_key == "local")
            else ("remote" if has_key else "")
        )
        from src.users.manager import is_admin
        from src.backend.routes.execution.review import _get_review_state

        return jsonify(
            {
                "ok": True,
                "version": "1.0.0",
                "user": uid or "",
                "running": running,
                "paused": paused,
                "paused_step": _get_paused_step(_current_proj_for_pause),
                "thinking_active": _thinking.get("active", False),
                "thinking_name": _thinking.get("name", ""),
                "thinking_elapsed": round(elapsed, 1),
                "active_runs": active_runs,
                "progress": progress,
                "has_key": has_key,
                "provider": provider,
                "key_mode": key_mode,
                "is_admin": uid and is_admin(uid),
                "review_state": _get_review_state(),  # ★ P1-6
                "case_review_state": _get_case_review_state_lazy(),  # ★ 测试用例审核闸门
                "version_warning": _get_version_warning(),  # ★ 版本一致性警告
            }
        )
    except Exception as e:
        _log.error("[api_status] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


# ══════════════════════════════════════════════════════════
# ★ 版本一致性确认（Step 3 启动时前端弹窗）
# ══════════════════════════════════════════════════════════


@exec_bp.route("/api/version-check/confirm", methods=["POST"])
def api_version_check_confirm():
    """用户确认版本不一致后继续执行。"""
    from src.models.permission import check_admin_or_permission
    from src.backend.state import _current_project

    ok, err = check_admin_or_permission("execution.run", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.state import (
        _set_version_warning,
        _set_paused_step,
        _current_project,
    )
    from src.pipeline.utils.pause_controller import pause_controller

    _set_version_warning({"active": False, "pending": False, "confirmed": True})
    _set_paused_step(0, _current_project())
    pause_controller.resume()
    _log.info("[版本检查] 用户确认继续，解除暂停")
    return jsonify({"ok": True, "message": "已确认，继续建模测试用例"})


@exec_bp.route("/api/version-check/cancel", methods=["POST"])
def api_version_check_cancel():
    """用户取消，跳过 Step 3 测试用例建模。"""
    from src.models.permission import check_admin_or_permission
    from src.backend.state import _current_project

    ok, err = check_admin_or_permission("execution.run", project=_current_project())
    if not ok:
        return jsonify({"ok": False, "error": err}), (
            401 if err.startswith("请先登录") else 403
        )
    from src.backend.state import _set_version_warning, _set_paused_step
    from src.pipeline.utils.pause_controller import pause_controller

    _set_version_warning({"active": False, "pending": False, "confirmed": False})
    _set_paused_step(0, _current_project())
    pause_controller.resume()
    _log.info("[版本检查] 用户取消，跳过 Step 3")
    return jsonify({"ok": True, "message": "已取消，跳过测试用例建模"})
