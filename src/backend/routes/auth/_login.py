# -*- coding: utf-8 -*-
"""
登录 / 登出 / 当前用户
"""

from flask import jsonify, session
import time as _time

from src.backend.routes.auth._core import auth_bp, _log
from src.backend.state import (
    _is_running,
    _active_runs,
    _active_runs_lock,
    _run_progress,
    _run_progress_lock,
)


def _log_action_safe(user_id: str, action: str, scope_type: str = "system"):
    """后台线程安全地记录操作日志（失败不影响主流程）。"""
    try:
        from src.users.manager import log_action

        log_action(user_id, action, scope_type=scope_type)
    except Exception as _e:
        _log.warning("[login] 后台 log_action 失败: %s", str(_e)[:120])


@auth_bp.route("/api/login", methods=["POST"])
def api_login():
    """用户登录。成功后设置 Flask session + 线程本地用户。"""
    from src.users.manager import login as user_login
    from src.core.config_loader import set_current_user
    from flask import request

    data = request.get_json(silent=True) or {}
    # ★ 根因修复：字段语义是"用户名"但键名叫 user_id（反直觉，LoginSchema
    #   的 description 都写着"用户名"），历史脚本 logs/_dom_diag*.py 等反复写成
    #   username 撞 401/400 再改回来——同一坑踩了无数次。此处入口归一化：接受
    #   user_id（规范键）或 username（通用约定别名），整个错误类别从此消灭。
    user_id = str(data.get("user_id") or data.get("username") or "").strip()
    password = data.get("password", "")
    if not user_id or not password:
        return jsonify(
            {
                "ok": False,
                "error": "缺少登录字段：需 user_id（或别名 username）+ password",
                "missing": [k for k, v in (("user_id", user_id), ("password", password)) if not v],
            }
        )
    # ★ 输入长度限制（防止 DoS）
    if len(user_id) > 64:
        return jsonify({"ok": False, "error": "用户名过长"})
    if len(password) > 256:
        return jsonify({"ok": False, "error": "密码过长"})
    # ★ 退出冷却期：3 秒内拒绝已登出用户的自动登录（防止密码管理器自动提交）
    #   仅对确实执行过 /api/logout 的 session 生效，未登录过的新 session 不受影响。
    if session.get("logged_out"):
        logout_time = session.get("logout_time", 0)
        if logout_time:
            elapsed = _time.time() - logout_time
            if elapsed < 3:
                _log.info(
                    f"[LOGIN BLOCKED] 用户 {user_id} 退出仅 {elapsed:.1f}s，冷却期内拒绝自动登录"
                )
                return jsonify({"ok": False, "error": "请稍后再试"})
    # ★ 获取客户端 IP 用于频率限制（仅取 X-Forwarded-For 的第一个 IP，防止伪造）
    ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.remote_addr
        or ""
    )
    # ★ User-Agent 写入 login_tracking（user_agent 列 NOT NULL，必须传）
    ua = request.headers.get("User-Agent", "")[:1000]
    # ★ 账户锁定检查（在调用 login 前检查，提供精确错误信息）
    from src.users.auth.rate_limit import check_login_rate

    ok_lock, err_lock = check_login_rate(user_id)
    if not ok_lock:
        _log.info(f"[LOGIN BLOCKED] 用户 {user_id} 账户锁定: {err_lock}")
        return jsonify({"ok": False, "error": err_lock})
    user = user_login(
        user_id, password, ip_address=ip, user_agent=ua, skip_rate_check=True
    )
    if user:
        # ★ 防止 session fixation：清除旧 session 再设置新值
        session.clear()
        # ★ Flask session 持久化 + 滑动过期参数
        session["user_id"] = user_id
        session["login_ts"] = _time.time()  # 登录时刻（8h 硬上限基准）
        session["last_activity"] = _time.time()  # 最后活动时间（滑动续期基准）
        session.permanent = True
        set_current_user(user_id)
        # ★ 重置全局项目/版本状态，防止跨用户数据泄露
        from src.backend.state import _set_current_project, _set_current_version

        _set_current_project("")
        _set_current_version("")
        # ★ 登录自动恢复上次项目（2026-08-31）：用户级记忆最近选择，
        #   登录后如仍有权访问则直接写回 session 与全局，避免“点击啥都跳项目列表”。
        #   未选过 / 已无权限的项目不回填，仍按流程先引导到项目管理。
        try:
            from src.users.manager import (
                get_user_last_project,
                get_user_projects,
            )

            _user_projs = get_user_projects(user_id) or []
            _last_proj = get_user_last_project(user_id) or ""
            if _last_proj and _last_proj in _user_projs:
                session["project_name"] = _last_proj
                _set_current_project(_last_proj)
        except Exception as _restore_e:
            _log.warning("[login] 恢复上次项目失败: %s", str(_restore_e)[:120])
        # ★ log_action 异步化：不阻塞登录响应返回
        import threading

        threading.Thread(
            target=lambda: _log_action_safe(user_id, "login"),
            daemon=True,
            name=f"log-action-{user_id}",
        ).start()
        # ★ 从 user_roles 获取角色名
        from src.users.manager import _conn as _uconn

        _c = _uconn()
        try:
            _role_row = _c.execute(
                "SELECT r.name role FROM user_roles ur JOIN roles r ON ur.role_id=r.id WHERE ur.user_id=%s",
                (user_id,),
            ).fetchone()
        finally:
            if _c:
                try:
                    _c.close()
                except Exception as _ce:
                    _log.warning("[login] 关闭角色查询连接失败: %s", str(_ce)[:120])
        role = _role_row["role"] if _role_row else "user"
        # ★ 缓存已砍：不再后台预热模型列表，每次前端请求实时拉取
        return jsonify(
            {
                "ok": True,
                "user": {
                    "id": user_id,
                    "name": user.get("name", user_id),
                    "role": role,
                },
            }
        )
    return jsonify({"ok": False, "error": "用户名或密码错误"})


@auth_bp.route("/api/logout", methods=["POST"])
def api_logout():
    from src.core.config_loader import (
        get_current_user,
        set_current_user,
        clear_session_key,
    )

    uid = get_current_user()
    if _is_running():
        with _active_runs_lock:
            for rid in _active_runs:
                _active_runs[rid]["force_stop"] = True
        _log.info(
            "[SYSTEM] 用户退出，已标记 %d 个活跃任务为强制停止", len(_active_runs)
        )
    if uid:
        from src.users.manager import log_action

        log_action(uid, "logout", scope_type="system")
    set_current_user(None)
    clear_session_key()
    # ★ 退出方案：不在 session 中 pop 键（save_session 无法可靠删除旧 cookie），
    #    而是把认证字段设为 None，加 logged_out 标记，让 save_session 保存更新后的 session。
    #    before_request 检查 logged_out 标记，阻止用户和项目的恢复。
    session["user_id"] = None
    session["login_ts"] = None
    session["last_activity"] = None
    session["project_name"] = None
    session["logged_out"] = True
    session["logout_time"] = _time.time()  # ★ 服务端冷却期：3 秒内拒绝登录
    # _permanent 保留原值，确保 session 非空，save_session 走正常保存路径
    from src.backend.state import _set_current_project

    _set_current_project("")  # 清除模块级全局变量
    _log.info(f"[LOGOUT] 用户 {uid} 已退出，session 已标记 logged_out")
    with _run_progress_lock:
        _run_progress.clear()
    return jsonify({"ok": True})


@auth_bp.route("/api/current-user")
def api_current_user():
    try:
        from src.core.config_loader import get_current_user, set_current_user
        from src.users.manager import get_user_by_id

        # /api/current-user 属于登录白名单，会绕过 app.before_request 的
        # session -> thread-local 同步；这里必须直接从 Flask session 恢复，
        # 否则登录接口成功后刷新页面仍会显示未登录。
        uid = session.get("user_id") or get_current_user()
        if uid:
            set_current_user(uid)
        if not uid:
            return jsonify({"user": None})
        u = get_user_by_id(uid)
        if u:
            return jsonify(
                {"user": {"id": u["id"], "name": u["name"], "role": u["role"]}}
            )
        return jsonify({"user": None})
    except Exception as _e:
        _log.error("[api_current_user] 异常: %s", _e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500
