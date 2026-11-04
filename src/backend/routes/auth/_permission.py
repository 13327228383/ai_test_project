# -*- coding: utf-8 -*-
"""
功能权限 API（按钮级访问控制）
"""

from flask import jsonify, request

from src.backend.routes.auth._core import auth_bp


@auth_bp.route("/api/permissions")
def api_get_permissions():
    """获取当前用户的权限列表。前端用于控制按钮显隐。

    返回字段：
    - permissions: 系统级权限列表
    - project_permissions: 当前项目内的权限列表（基于 project_members 身份）
    - project_level: 当前项目内的身份（owner/member/空）
    - is_admin: 是否系统管理员
    - is_project_admin: 是否项目管理员
    - is_project_member: 是否项目成员
    - system_role: 系统角色（admin/project_admin/project_member/空）
    - all_permissions: 全部权限定义（用于权限管理面板）
    """
    from src.core.config_loader import get_current_user

    uid = get_current_user()
    if not uid:
        return jsonify(
            {
                "permissions": [],
                "project_permissions": [],
                "project_level": "",
                "is_admin": False,
                "is_project_admin": False,
                "is_project_member": False,
                "system_role": "",
            }
        )
    from src.models.permission import (
        get_user_permissions,
        get_all_permissions,
        PROJECT_LEVEL_PERMISSIONS,
    )
    from src.users.manager import (
        is_admin,
        is_project_admin,
        is_project_member,
        get_user_system_role,
    )
    from src.backend.state import _current_project
    from src.models.base import get_conn

    perms = sorted(list(get_user_permissions(uid)))
    proj = _current_project() or ""
    level = ""
    project_perms = []
    if proj:
        if is_admin(uid):
            level = "owner"
            project_perms = sorted(
                list(PROJECT_LEVEL_PERMISSIONS.get("owner", set()))
            )
        else:
            c = get_conn()
            try:
                row = c.execute(
                    "SELECT role FROM project_members WHERE user_id=%s AND project=%s",
                    (uid, proj),
                ).fetchone()
                if row:
                    level = row["role"]
                    project_perms = sorted(
                        list(PROJECT_LEVEL_PERMISSIONS.get(level, set()))
                    )
            finally:
                c.close()
    return jsonify(
        {
            "permissions": perms,
            "project_permissions": project_perms,
            "project_level": level,
            "is_admin": is_admin(uid),
            "is_project_admin": is_project_admin(uid),
            "is_project_member": is_project_member(uid),
            "system_role": get_user_system_role(uid),
            "all_permissions": get_all_permissions(),
        }
    )


@auth_bp.route("/api/permissions/role/<role>")
def api_get_role_permissions(role):
    """获取指定角色的权限列表（管理员用）。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, is_project_admin

    uid = get_current_user()
    if not uid or (not is_admin(uid) and not is_project_admin(uid)):
        return jsonify({"ok": False, "error": "仅项目管理员及以上可查看"}), 403
    from src.models.permission import get_role_permissions_by_name

    return jsonify({"role": role, "permissions": get_role_permissions_by_name(role)})


@auth_bp.route("/api/permissions/role/<role>", methods=["PUT"])
def api_set_role_permission(role):
    """授予或撤销某角色的某权限（管理员用）。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin

    uid = get_current_user()
    if not uid or not is_admin(uid):
        return jsonify({"ok": False, "error": "仅管理员可修改"}), 403
    data = request.get_json(silent=True) or {}
    permission = data.get("permission", "").strip()
    granted = data.get("granted", True)
    if not permission:
        return jsonify({"ok": False, "error": "缺少 permission"}), 400
    from src.models.permission import set_role_permission_by_name

    set_role_permission_by_name(role, permission, granted)
    return jsonify(
        {"ok": True, "role": role, "permission": permission, "granted": granted}
    )


@auth_bp.route("/api/users/<user_id>/role", methods=["PUT"])
def api_set_user_role(user_id):
    """设置用户角色。需要管理员权限或 user.role.set 权限。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, set_user_role
    from src.models.permission import has_permission
    from src.users._user_db_core import ALLOWED_ROLES

    uid = get_current_user()
    if not uid or (not is_admin(uid) and not has_permission(uid, "user.role.set")):
        return (
            jsonify(
                {"ok": False, "error": "无权修改用户角色（需要权限: user.role.set）"}
            ),
            403,
        )
    data = request.get_json(silent=True) or {}
    new_role = data.get("role", "").strip()
    if new_role not in ALLOWED_ROLES:
        return (
            jsonify({"ok": False, "error": f"角色必须为 {', '.join(ALLOWED_ROLES)}"}),
            400,
        )
    ok = set_user_role(user_id, new_role)
    if ok:
        from src.users.manager import log_action

        log_action(
            uid, "set-role", scope_type="user", scope_id=user_id, detail=f"→ {new_role}"
        )
        return jsonify({"ok": True, "user_id": user_id, "role": new_role})
    return (
        jsonify({"ok": False, "error": "操作失败（可能是最后一位管理员不可降级）"}),
        400,
    )
