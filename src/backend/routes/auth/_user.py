# -*- coding: utf-8 -*-
"""
用户 CRUD / 项目授权 / 执行记录
"""

import re
from flask import jsonify, request

from src.backend.routes.auth._core import auth_bp, _log
from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission


@auth_bp.route("/api/users", methods=["GET"])
def api_list_users():
    """列出用户。未登录时返回基本信息（id/name）供登录页下拉使用；
    已登录管理员返回完整信息（含角色、创建时间）。"""
    try:
        from src.core.config_loader import get_current_user
        from src.users.user_db import list_users as um_list
        from src.users.manager import is_admin, get_user_by_id
        from src.models.permission import has_permission
        from src.users._user_db_core import _conn

        uid = get_current_user()
        # ★ 未登录：登录下拉只查 users 表，不走 list_users() 的 user_roles JOIN
        if not uid:
            c = _conn()
            try:
                rows = c.execute(
                    "SELECT id, name FROM users ORDER BY created_at"
                ).fetchall()
                users = [{"id": r["id"], "name": r["name"]} for r in rows]
            finally:
                c.close()
            return jsonify({"ok": True, "users": users})
        # ★ 已登录但非管理员：仅返回自己的信息，不查全量
        if not is_admin(uid) and not has_permission(uid, "user.manage"):
            u = get_user_by_id(uid)
            return jsonify(
                {
                    "ok": True,
                    "users": [{"id": u["id"], "name": u["name"]}] if u else [],
                }
            )
        # ★ 已登录管理员：返回完整全量信息
        users = um_list()
        return jsonify(
            {
                "ok": True,
                "users": [
                    {
                        "id": u["id"],
                        "name": u["name"],
                        "role": u["role"],
                        "created_at": u.get("created_at", ""),
                    }
                    for u in users
                ],
            }
        )
    except Exception as e:
        _log.error("[api_list_users] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


@auth_bp.route("/api/users", methods=["POST"])
def api_create_user():
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin
    from src.models.permission import has_permission

    uid = get_current_user()
    if not is_admin(uid) and not has_permission(uid, "user.manage"):
        return (
            jsonify({"ok": False, "error": "无权创建用户（需要权限: user.manage）"}),
            403,
        )
    from src.users.manager import create_user as um_create, validate_password_strength

    data = request.get_json(silent=True) or {}
    # ★ 2026-09-22 同族归一化：user_id 为规范键，username 别名与 /api/login 一致
    user_id = str(data.get("user_id") or data.get("username") or "").strip()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if not user_id or not name or not password:
        return jsonify(
            {
                "ok": False,
                "error": "缺少字段：需 user_id（或别名 username）+ name + password",
            }
        )
    # ★ 输入长度限制
    if len(user_id) > 64:
        return jsonify({"ok": False, "error": "用户名不能超过 64 个字符"})
    if len(name) > 64:
        return jsonify({"ok": False, "error": "显示名不能超过 64 个字符"})
    # ★ user_id 格式校验（与 user_crud.py:16 一致，提前到路由层给出精确错误）
    if not re.match(r"^[a-zA-Z0-9_\-一-鿿]+$", user_id):
        return jsonify({"ok": False, "error": "用户名不能包含空格和特殊符号"}), 400
    # ★ 后端密码强度校验 (api_create_user)
    ok_pw, err_pw = validate_password_strength(password)
    if not ok_pw:
        return jsonify({"ok": False, "error": err_pw})
    user = um_create(user_id, name, password, data.get("role", "user"))
    if user:
        return jsonify({"ok": True, "user_id": user_id})
    return jsonify({"ok": False, "error": "用户已存在或密码不符合要求"})


# ─────────────────────────────────────────────────────────────
# ★ v3: 注册接口 — 支持自注册和管理员注册
# 设计原则：
#   - 未登录自注册：创建 project_member，不分配项目
#   - admin 注册：可选任意角色 + 任意项目
#   - project_admin 注册：只能创建 project_member + 只能分配自己管理的项目
# ─────────────────────────────────────────────────────────────
@auth_bp.route("/api/my-manageable-projects")
def api_my_manageable_projects():
    """获取当前用户可管理的项目列表。
    admin → 全部 active 项目；project_admin → 自己为 owner 的项目；其他 → 空。
    """
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, get_user_system_role, _conn

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    c = _conn()
    try:
        if is_admin(uid):
            rows = c.execute(
                "SELECT name FROM projects WHERE status='active' ORDER BY name"
            ).fetchall()
            projects = [r["name"] for r in rows if r.get("name")]
        else:
            # project_admin 只返回自己为 owner 的项目
            rows = c.execute(
                "SELECT project FROM project_members WHERE user_id=%s AND role='owner' ORDER BY project",
                (uid,),
            ).fetchall()
            projects = [r["project"] for r in rows]
    finally:
        if c:
            try:
                c.close()
            except Exception as _e:
                _log.debug("关闭连接失败: %s", _e)
    return jsonify({"projects": projects})


@auth_bp.route("/api/register", methods=["POST"])
def api_register():
    """注册接口 — 支持自注册和管理员注册。
    v3: 两层权限模型
    - 未登录自注册：创建 project_member，不分配项目
    - admin 注册：可选任意角色 + 任意项目
    - project_admin 注册：只能创建 project_member + 只能分配自己管理的项目
    """
    from src.users.manager import (
        create_user as um_create,
        validate_password_strength,
        get_user_by_id,
    )
    from src.core.config_loader import get_current_user
    from src.users.manager import (
        is_admin,
        get_user_system_role,
        assign_project,
        get_user_projects,
    )

    data = request.get_json(silent=True) or {}
    # ★ 2026-09-22 同族归一化：user_id 为规范键，username 别名与 /api/login 一致
    user_id = str(data.get("user_id") or data.get("username") or "").strip()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if not user_id or not name or not password:
        return jsonify(
            {
                "ok": False,
                "error": "缺少字段：需 user_id（或别名 username）+ name + password",
            }
        ), 400
    if len(user_id) > 64:
        return jsonify({"ok": False, "error": "用户名不能超过 64 个字符"}), 400
    if len(name) > 64:
        return jsonify({"ok": False, "error": "显示名不能超过 64 个字符"}), 400
    # ★ user_id 格式校验（与 user_crud.py:16 一致，提前到路由层给出精确错误）
    if not re.match(r"^[a-zA-Z0-9_\-一-鿿]+$", user_id):
        return jsonify({"ok": False, "error": "用户名不能包含空格和特殊符号"}), 400
    ok_pw, err_pw = validate_password_strength(password)  # api_register
    if not ok_pw:
        return jsonify({"ok": False, "error": err_pw}), 400
    if get_user_by_id(user_id):
        return jsonify({"ok": False, "error": "用户名已被使用"}), 409

    # 判断调用者身份
    caller_uid = get_current_user()
    caller_role = get_user_system_role(caller_uid) if caller_uid else ""
    requested_role = data.get("role", "").strip()
    project_ids = data.get("project_ids", [])

    # 根据调用者身份确定角色
    if caller_role == "admin":
        # admin 可选任意角色
        role = (
            requested_role
            if requested_role in ("admin", "project_admin", "project_member")
            else "project_member"
        )
    elif caller_role == "project_admin":
        # project_admin 只能创建 project_member
        role = "project_member"
    else:
        # ★ PV-003: 自注册默认 viewer 角色（最受限），防止权限提升
        role = "viewer"

    user = um_create(user_id, name, password, role=role)
    if not user:
        return (
            jsonify(
                {"ok": False, "error": "注册失败，用户名可能已存在或密码不符合要求"}
            ),
            400,
        )

    # 项目分配
    if project_ids and caller_uid:
        # project_admin 只能分配自己管理的项目
        if caller_role == "project_admin":
            my_projects = set(get_user_projects(caller_uid))
            project_ids = [p for p in project_ids if p in my_projects]
        for proj in project_ids:
            try:
                assign_project(proj, user_id, "member", added_by=caller_uid)
            except Exception as e:
                _log.warning(
                    "[api_register] 项目分配失败 %s → %s: %s", user_id, proj, e
                )

    _log.info(
        "[api_register] 新用户注册成功: %s (%s) role=%s projects=%s caller=%s",
        user_id,
        name,
        role,
        len(project_ids),
        caller_role or "self",
    )

    # ★ PV-004: 自注册后自动登录
    if not caller_uid:
        from src.core.config_loader import set_current_user, set_session_key
        from flask import session as _flask_session
        import time as _time

        _flask_session.clear()
        _flask_session["user_id"] = user_id
        _flask_session["login_ts"] = _time.time()
        _flask_session["last_activity"] = _time.time()
        _flask_session.permanent = True
        set_current_user(user_id)
        set_session_key("local")
        _log.info("[api_register] 自注册用户 %s 已自动登录", user_id)

    return jsonify({"ok": True, "user_id": user_id, "message": "注册成功，请登录"})


@auth_bp.route("/api/users/<user_id>", methods=["PUT"])
def api_update_user(user_id):
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin
    from src.models.permission import has_permission

    uid = get_current_user()
    if not uid:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    # 自己改自己（改密码）→ 需验证旧密码；改别人 → 需要 user.manage 权限
    if uid != user_id and not is_admin(uid) and not has_permission(uid, "user.manage"):
        return (
            jsonify(
                {"ok": False, "error": "无权修改其他用户（需要权限: user.manage）"}
            ),
            403,
        )

    data = request.get_json(silent=True) or {}

    # ★ 安全修复：修改密码时必须验证旧密码
    if "password" in data and data["password"]:
        old_password = data.get("old_password", "")
        if not old_password:
            return jsonify({"ok": False, "error": "修改密码需提供旧密码"}), 400
        from src.users.auth import _verify_password
        from src.users.user_db import _conn

        c = _conn()
        row = c.execute("SELECT password FROM users WHERE id=%s", (user_id,)).fetchone()
        c.close()
        if not row or not _verify_password(old_password, row["password"]):
            return jsonify({"ok": False, "error": "旧密码不正确"}), 403
        # 新旧密码不能相同
        if old_password == data["password"]:
            return jsonify({"ok": False, "error": "新密码不能与旧密码相同"}), 400

    from src.users.manager import update_user as um_update

    ok, err_msg = um_update(user_id, **data)
    if not ok:
        return jsonify({"ok": False, "error": err_msg})
    return jsonify({"ok": True})


@auth_bp.route("/api/users/<user_id>", methods=["DELETE"])
def api_delete_user(user_id):
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin
    from src.models.permission import has_permission

    uid = get_current_user()
    # ★ 安全修复：禁止用户删除自己（防止管理员误删自己的登录态）
    if uid and uid == user_id:
        return (
            jsonify(
                {"ok": False, "error": "不能删除当前登录用户，请由其他管理员执行此操作"}
            ),
            400,
        )
    if not is_admin(uid) and not has_permission(uid, "user.manage"):
        return (
            jsonify({"ok": False, "error": "无权删除用户（需要权限: user.manage）"}),
            403,
        )
    from src.users.manager import delete_user as um_delete

    ok = um_delete(user_id)
    return jsonify({"ok": ok})


# ── 项目授权（旧 /api/project-access 路由已废弃并删除，请使用角色管理 API /api/roles 或 /api/users/<id>/roles） ──


@auth_bp.route("/api/user-projects/<user_id>")
def api_user_projects(user_id):
    """获取某用户已分配的项目列表。"""
    try:
        ok, err = check_admin_or_permission(
            "project.config.view", project=_current_project()
        )
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )
        from src.users.manager import get_user_projects

        return jsonify(
            {"ok": True, "user_id": user_id, "projects": get_user_projects(user_id)}
        )
    except Exception as e:
        _log.error("[api_user_projects] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


# ── 执行记录 ──
@auth_bp.route("/api/executions")
def api_executions():
    """获取当前用户的历史执行记录。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import get_user_executions

    uid = get_current_user()
    proj = request.args.get("project") or _current_project()
    return jsonify(get_user_executions(uid, proj))
