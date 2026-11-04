# -*- coding: utf-8 -*-
"""角色与 RBAC 管理模块 (package facade)"""
from ._user_role import (
    set_user_role,
    promote_to_admin,
    promote_to_project_admin,
    set_as_project_member,
    demote_from_admin,
    get_admin_count,
)
from ._role_crud import (
    create_role,
    update_role,
    delete_role,
    get_all_roles,
    get_role,
)
from ._permission import (
    get_role_permissions,
    set_role_permission,
    set_role_permissions_bulk,
    get_role_projects,
    get_role_project_names,
    set_role_project,
    set_role_projects_bulk,
    get_user_roles,
    assign_role_to_user,
    remove_role_from_user,
    set_user_roles_bulk,
)

# 兼容别名：外部验证命令要求的名称
assign_permission = set_role_permission
revoke_permission = set_role_permission
list_roles = get_all_roles

__all__ = [
    "set_user_role",
    "promote_to_admin",
    "promote_to_project_admin",
    "set_as_project_member",
    "demote_from_admin",
    "get_admin_count",
    "create_role",
    "update_role",
    "delete_role",
    "get_all_roles",
    "get_role",
    "get_role_permissions",
    "set_role_permission",
    "set_role_permissions_bulk",
    "get_role_projects",
    "get_role_project_names",
    "set_role_project",
    "set_role_projects_bulk",
    "get_user_roles",
    "assign_role_to_user",
    "remove_role_from_user",
    "set_user_roles_bulk",
    # 兼容别名
    "assign_permission",
    "revoke_permission",
    "list_roles",
]
