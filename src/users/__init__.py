# -*- coding: utf-8 -*-
"""
用户管理模块
============
统一管理用户、项目授权、执行记录。
DB: MySQL (ai_test_engine) via db_manager

拆分为:
- auth.py     — 加密 + 密码 + 登录
- user_db.py  — DB连接 + 用户CRUD + 角色 + 项目授权 + 凭证
- audit.py    — 执行跟踪
- manager.py  — 兼容层（重新导出所有公开函数）
"""
from src.users.auth import (
    validate_password_strength,
    login,
    has_api_key,
    get_user_api_key,
    _hash,
    _encrypt_auto,
    _decrypt_auto,
)

from src.users.user_db import (
    _conn,
    create_user,
    list_users,
    list_login_users,
    update_user,
    delete_user,
    assign_project,
    revoke_project,
    get_user_projects,
    can_view,
    is_admin,
    is_project_admin,
    is_project_member,
    get_user_system_role,
    get_project_level,
    log_action,
    set_user_role,
    promote_to_admin,
    promote_to_project_admin,
    set_as_project_member,
    demote_from_admin,
    get_admin_count,
    create_role,
    update_role,
    delete_role,
    get_all_roles,
    get_role,
    get_role_permissions,
    set_role_permission,
    set_role_permissions_bulk,
    get_role_projects,
    get_role_project_names,
    set_role_project,
    set_role_projects_bulk,
    create_project_version,
    activate_project_version,
    get_active_version,
    get_active_version_label,
    get_project_versions,
    update_project_version,
    archive_project_version,
    get_user_roles,
    assign_role_to_user,
    remove_role_from_user,
    set_user_roles_bulk,
    record_config_change,
    get_config_history,
    rollback_config,
    record_document_version,
    get_document_versions,
    get_user_credential,
    set_user_credential,
    delete_user_credential,
    get_all_user_credentials,
)

from src.users.audit import (
    record_execution,
    get_execution_status_map,
    is_executed_by_user,
    get_executed_rows,
    get_failed_rows,
    get_user_executions,
    get_all_executions,
    get_project_executors,
    get_execution_records,
)
