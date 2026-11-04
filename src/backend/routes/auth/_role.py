# -*- coding: utf-8 -*-
"""
角色管理 API（RBAC 核心 — 角色 CRUD + 权限/项目勾选 + 用户-角色分配）
"""

from flask import jsonify, request

from src.backend.routes.auth._core import auth_bp, _log


# ═══════════════════════════════════════════════════════════════
# 角色 CRUD
# ═══════════════════════════════════════════════════════════════


@auth_bp.route("/api/roles")
def api_list_roles():
    """列出所有角色（含用户数统计）。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, is_project_admin, get_all_roles

    uid = get_current_user()
    if not uid or (not is_admin(uid) and not is_project_admin(uid)):
        return jsonify({"ok": False, "error": "仅项目管理员及以上可查看角色"}), 403
    roles = get_all_roles()
    return jsonify({"ok": True, "roles": roles})


@auth_bp.route("/api/roles", methods=["POST"])
def api_create_role():
    """新建自定义角色。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, create_role, log_action

    uid = get_current_user()
    if not uid or not is_admin(uid):
        return jsonify({"ok": False, "error": "仅管理员可创建角色"}), 403
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    description = data.get("description", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "角色名不能为空"}), 400
    rid = create_role(name, description)
    if rid:
        log_action(
            uid, "role-create", scope_type="system", detail=f"role_id={rid} name={name}"
        )
        return jsonify({"ok": True, "role_id": rid})
    return jsonify({"ok": False, "error": "创建失败（角色名可能已存在）"}), 400


@auth_bp.route("/api/roles/<role_id>", methods=["PUT"])
def api_update_role(role_id):
    """更新角色（名称/描述）。系统角色仅可改描述。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, update_role, log_action

    uid = get_current_user()
    if not uid or not is_admin(uid):
        return jsonify({"ok": False, "error": "仅管理员可编辑角色"}), 403
    data = request.get_json(silent=True) or {}
    ok = update_role(
        role_id, **{k: v for k, v in data.items() if k in ("name", "description")}
    )
    if ok:
        log_action(uid, "role-update", scope_type="system", detail=f"role_id={role_id}")
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "更新失败（系统角色不可改名）"}), 400


@auth_bp.route("/api/roles/<role_id>", methods=["DELETE"])
def api_delete_role(role_id):
    """删除自定义角色（系统角色不可删）。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, delete_role, log_action

    uid = get_current_user()
    if not uid or not is_admin(uid):
        return jsonify({"ok": False, "error": "仅管理员可删除角色"}), 403
    ok = delete_role(role_id)
    if ok:
        log_action(uid, "role-delete", scope_type="system", detail=f"role_id={role_id}")
        return jsonify({"ok": True})
    return (
        jsonify({"ok": False, "error": "删除失败（系统角色不可删除或角色不存在）"}),
        400,
    )


# ── 角色权限管理 ──


@auth_bp.route("/api/roles/<role_id>/permissions")
def api_get_role_perms_by_id(role_id):
    """获取某角色的所有功能权限状态。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, is_project_admin, get_role_permissions

    uid = get_current_user()
    if not uid or (not is_admin(uid) and not is_project_admin(uid)):
        return jsonify({"ok": False, "error": "仅项目管理员及以上可查看"}), 403
    perms = get_role_permissions(role_id)
    from src.models.permission import (
        PERMISSION_DEFINITIONS,
        SYSTEM_ADMIN_ONLY_PERMISSIONS,
    )

    # 非admin用户过滤掉admin专属权限
    is_admin_user = is_admin(uid)
    # 按类别分组
    groups = {}
    for perm_id, granted in perms.items():
        # 非admin用户不显示admin专属权限
        if not is_admin_user and perm_id in SYSTEM_ADMIN_ONLY_PERMISSIONS:
            continue
        category = perm_id.split(".")[0]
        if category not in groups:
            groups[category] = []
        groups[category].append(
            {
                "id": perm_id,
                "description": PERMISSION_DEFINITIONS.get(perm_id, perm_id),
                "granted": granted,
                "admin_only": perm_id in SYSTEM_ADMIN_ONLY_PERMISSIONS,
            }
        )
    return jsonify(
        {
            "role_id": role_id,
            "permissions": perms,
            "groups": groups,
            "admin_only_perm_ids": sorted(SYSTEM_ADMIN_ONLY_PERMISSIONS),
        }
    )


@auth_bp.route("/api/roles/<role_id>/permissions", methods=["PUT"])
def api_set_role_permissions(role_id):
    """批量设置角色的功能权限。Body: {permissions: {perm_id: true/false, ...}}"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, set_role_permissions_bulk, log_action

    uid = get_current_user()
    if not uid or not is_admin(uid):
        return jsonify({"ok": False, "error": "仅管理员可修改"}), 403
    data = request.get_json(silent=True) or {}
    perms = data.get("permissions", {})
    if not perms:
        return jsonify({"ok": False, "error": "缺少 permissions"}), 400
    # 安全校验：管理员专属权限禁止授予自定义角色。
    # B方案(2026-09-01)：不再静默跳过；若请求显式授予 admin 专属权限则直接报错，避免"假全选假安全感"。
    from src.models.permission import SYSTEM_ADMIN_ONLY_PERMISSIONS, PERMISSION_DEFINITIONS

    granted_admin_only = [
        pid
        for pid, granted in perms.items()
        if granted and pid in SYSTEM_ADMIN_ONLY_PERMISSIONS
    ]
    if granted_admin_only:
        names = "、".join(
            PERMISSION_DEFINITIONS.get(p, p) for p in sorted(granted_admin_only)
        )
        return (
            jsonify(
                {
                    "ok": False,
                    "error": f"以下为管理员专属权限，不可授予自定义角色：{names}",
                }
            ),
            400,
        )
    # 仅保留非 admin 专属权限（granted=false 的无害项一并过滤）
    filtered_perms = {
        pid: granted
        for pid, granted in perms.items()
        if pid not in SYSTEM_ADMIN_ONLY_PERMISSIONS
    }
    ok = set_role_permissions_bulk(role_id, filtered_perms)
    if ok:
        log_action(
            uid,
            "role-perms-set",
            scope_type="system",
            detail=f"role_id={role_id} count={len(filtered_perms)}",
        )
        granted = sum(1 for v in filtered_perms.values() if v)
        return jsonify({"ok": True, "updated": granted, "total": len(filtered_perms)})
    return jsonify({"ok": False, "error": "设置失败"}), 400


# ── 用户-角色分配 ──
# v3: 角色项目管理端点已删除（role_projects 表已废弃，项目成员关系改为 project_members 表按用户绑定）
# 原 GET/PUT /api/roles/<role_id>/projects 两个端点已移除


@auth_bp.route("/api/users/<user_id>/roles")
def api_get_user_roles(user_id):
    """获取某用户的所有角色。
    v3: admin 和 project_admin 可查看
    """
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, is_project_admin, get_user_roles

    uid = get_current_user()
    if not uid or (not is_admin(uid) and not is_project_admin(uid)):
        return jsonify({"ok": False, "error": "无权查看"}), 403
    user_roles = get_user_roles(user_id)
    from src.users.manager import get_all_roles

    all_roles = get_all_roles()
    assigned_ids = {r["id"] for r in user_roles}
    return jsonify(
        {
            "user_id": user_id,
            "roles": user_roles,
            "all_roles": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "description": r.get("description", ""),
                    "is_system": r.get("is_system", 0),
                    "user_count": r.get("user_count", 0),
                    "assigned": r["id"] in assigned_ids,
                }
                for r in all_roles
            ],
        }
    )


@auth_bp.route("/api/users/<user_id>/roles", methods=["PUT"])
def api_set_user_roles(user_id):
    """批量设置用户的角色列表。Body: {role_ids: ["admin", ...]}
    v3: admin 可设任意角色；project_admin 只能设 project_member
    """
    from src.core.config_loader import get_current_user
    from src.users.manager import (
        is_admin,
        is_project_admin,
        set_user_roles_bulk,
        log_action,
    )

    uid = get_current_user()
    if not uid or (not is_admin(uid) and not is_project_admin(uid)):
        return jsonify({"ok": False, "error": "无权修改用户角色"}), 403
    data = request.get_json(silent=True) or {}
    role_ids = data.get("role_ids", [])
    if not role_ids:
        return jsonify({"ok": False, "error": "至少需要一个角色"}), 400
    # project_admin 只能分配 project_member 角色
    if not is_admin(uid):
        role_ids = [
            "project_member" if rid == "project_member" else rid for rid in role_ids
        ]
        role_ids = [rid for rid in role_ids if rid == "project_member"]
        if not role_ids:
            return (
                jsonify({"ok": False, "error": "项目管理员只能分配项目成员角色"}),
                403,
            )
    ok = set_user_roles_bulk(user_id, role_ids)
    if ok:
        log_action(
            uid,
            "user-roles-set",
            scope_type="user",
            scope_id=user_id,
            detail=f"roles={role_ids}",
        )
        return jsonify({"ok": True, "user_id": user_id, "role_ids": role_ids})
    return jsonify({"ok": False, "error": "设置失败"}), 400


# ── 用户项目管理（v3: project_members 表） ──


@auth_bp.route("/api/users/<user_id>/projects")
def api_get_user_projects(user_id):
    """获取用户的项目列表 + 全部可选项目。
    v3: admin 可查看任意用户；project_admin 可查看（仅显示自己管理的项目）
    """
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, is_project_admin, get_user_projects, _conn

    uid = get_current_user()
    if not uid or (not is_admin(uid) and not is_project_admin(uid)):
        return jsonify({"ok": False, "error": "无权查看"}), 403
    # 用户已绑定的项目（含 level）
    c = _conn()
    try:
        user_proj_rows = c.execute(
            "SELECT project, role FROM project_members WHERE user_id=%s", (user_id,)
        ).fetchall()
        user_proj_map = {r["project"]: r["role"] for r in user_proj_rows}
    finally:
        if c:
            try:
                c.close()
            except Exception as _e:
                _log.debug("关闭连接失败: %s", _e)
    # 全部 active 项目
    c = _conn()
    try:
        all_proj_rows = c.execute(
            "SELECT name FROM projects WHERE status='active' ORDER BY name"
        ).fetchall()
        all_projects = [r["name"] for r in all_proj_rows if r.get("name")]
    finally:
        if c:
            try:
                c.close()
            except Exception as _e:
                _log.debug("关闭连接失败: %s", _e)
    return jsonify(
        {
            "user_id": user_id,
            "projects": [{"project": p, "level": l} for p, l in user_proj_map.items()],
            "all_projects": [
                {
                    "name": p,
                    "granted": p in user_proj_map,
                    "level": user_proj_map.get(p, ""),
                }
                for p in all_projects
            ],
        }
    )


@auth_bp.route("/api/users/<user_id>/projects", methods=["PUT"])
def api_set_user_projects(user_id):
    """批量设置用户的项目成员关系。Body: {projects: {"proj_name": {"granted": bool, "level": "member"}}}
    v3: admin 可设任意项目；project_admin 只能设自己为 owner 的项目
    """
    from src.core.config_loader import get_current_user
    from src.users.manager import (
        is_admin,
        is_project_admin,
        assign_project,
        revoke_project,
        get_user_projects,
        log_action,
    )

    uid = get_current_user()
    if not uid or (not is_admin(uid) and not is_project_admin(uid)):
        return jsonify({"ok": False, "error": "无权修改用户项目"}), 403
    data = request.get_json(silent=True) or {}
    projects = data.get("projects", {})
    if not projects:
        return jsonify({"ok": False, "error": "缺少 projects"}), 400
    # project_admin 只能操作自己管理的项目
    allowed_projects = set()
    if not is_admin(uid):
        allowed_projects = set(get_user_projects(uid))
    granted_count = 0
    errors = []
    for proj_name, val in projects.items():
        if isinstance(val, dict):
            granted = val.get("granted", False)
            level = val.get("level", "member")
        else:
            granted = bool(val)
            level = "member"
        if level not in ("owner", "member"):
            level = "member"
        # ★ 单 owner 约束：仅系统管理员可设/调换 owner，项目管理员只能设 member。
        if level == "owner" and not is_admin(uid):
            level = "member"
        # project_admin 只能操作自己管理的项目
        if not is_admin(uid) and proj_name not in allowed_projects:
            errors.append(proj_name)
            continue
        if granted:
            ok = assign_project(proj_name, user_id, level, added_by=uid)
            if ok:
                granted_count += 1
            else:
                errors.append(proj_name)
        else:
            revoke_project(proj_name, user_id)
    log_action(
        uid,
        "user-projects-set",
        scope_type="user",
        scope_id=user_id,
        detail=f"granted={granted_count} errors={len(errors)}",
    )
    if errors:
        return jsonify(
            {
                "ok": True,
                "updated": granted_count,
                "errors": errors,
                "total": len(projects),
            }
        )
    return jsonify({"ok": True, "updated": granted_count, "total": len(projects)})


# ── 项目成员管理（按项目维度） ──


@auth_bp.route("/api/projects/<project>/members")
def api_get_project_members(project):
    """获取项目成员列表。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, is_project_admin, _conn

    uid = get_current_user()
    if not uid or (not is_admin(uid) and not is_project_admin(uid)):
        return jsonify({"ok": False, "error": "仅项目管理员及以上可查看"}), 403
    c = _conn()
    try:
        rows = c.execute(
            "SELECT pm.user_id, pm.role, u.name FROM project_members pm "
            "LEFT JOIN users u ON pm.user_id = u.id "
            "WHERE pm.project=%s ORDER BY pm.role DESC, pm.user_id",
            (project,),
        ).fetchall()
        members = [
            {"user_id": r["user_id"], "name": r.get("name", ""), "role": r["role"]}
            for r in rows
        ]
    finally:
        if c:
            try:
                c.close()
            except Exception as _e:
                _log.debug("关闭连接失败: %s", _e)
    return jsonify({"project": project, "members": members})


@auth_bp.route("/api/projects/<project>/members", methods=["POST"])
def api_add_project_member(project):
    """添加项目成员。Body: {user_id, level: "member"/"owner"}"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, is_project_admin, assign_project, log_action

    uid = get_current_user()
    if not uid or (not is_admin(uid) and not is_project_admin(uid)):
        return jsonify({"ok": False, "error": "仅项目管理员及以上可操作"}), 403
    data = request.get_json(silent=True) or {}
    target_uid = data.get("user_id", "").strip()
    level = data.get("level", "member")
    if not target_uid:
        return jsonify({"ok": False, "error": "缺少 user_id"}), 400
    if level not in ("owner", "member"):
        level = "member"
    # ★ 单 owner 约束：仅系统管理员可设/调换 owner（项目至多一个管理员）。
    #   项目管理员只能添加/管理普通成员（member）。调换走 assign_project 内自动降级。
    if level == "owner" and (not uid or not is_admin(uid)):
        return jsonify({"ok": False, "error": "仅系统管理员可调整项目管理员"}), 403
    ok = assign_project(project, target_uid, level, added_by=uid)
    if ok:
        log_action(
            uid,
            "project-member-add",
            scope_type="project",
            scope_id=project,
            detail=f"user={target_uid} level={level}",
        )
        return jsonify(
            {"ok": True, "project": project, "user_id": target_uid, "level": level}
        )
    return jsonify({"ok": False, "error": "添加失败"}), 400


@auth_bp.route("/api/projects/<project>/members/<user_id>", methods=["DELETE"])
def api_remove_project_member(project, user_id):
    """移除项目成员。"""
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, is_project_admin, revoke_project, log_action

    uid = get_current_user()
    if not uid or (not is_admin(uid) and not is_project_admin(uid)):
        return jsonify({"ok": False, "error": "仅项目管理员及以上可操作"}), 403
    ok = revoke_project(project, user_id)
    if ok:
        log_action(
            uid,
            "project-member-remove",
            scope_type="project",
            scope_id=project,
            detail=f"user={user_id}",
        )
        return jsonify({"ok": True, "project": project, "user_id": user_id})
    return jsonify({"ok": False, "error": "移除失败"}), 400
