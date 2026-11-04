# -*- coding: utf-8 -*-
"""
用户管理器 — 兼容层
====================
此文件已拆分为三个模块:
- src/users/auth.py     — 加密 + 密码 + 登录
- src/users/user_db.py  — DB连接 + 用户CRUD + 角色 + 项目授权 + 凭证
- src/users/audit.py    — 执行跟踪

保留此文件以确保向后兼容 — 所有原有导入路径不变。
"""

# 从 auth.py 导出
from src.users.auth import (
    validate_password_strength,
    login,
    has_api_key,
    get_user_api_key,
    _hash,
    _encrypt_auto,
    _decrypt_auto,
    _load_or_create_key,
    _encrypt,
    _decrypt,
    check_login_rate,
    record_login_success,
    record_login_failure,
    _record_login_tracking,
    _fernet_instance,
    _fernet_lock,
    MAX_FAILED_ATTEMPTS,
    LOCK_DURATION_MINUTES,
)

# 从 user_db.py 导出
from src.users.user_db import (
    _conn,
    create_user,
    list_users,
    list_login_users,
    get_user_by_id,
    update_user,
    delete_user,
    get_user_last_project,
    set_user_last_project,
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
    ALLOWED_ROLES,
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
    record_source_document,
    get_source_document,
    save_screenshot,
    get_screenshot,
    get_screenshots_by_intent,
    get_user_credential,
    set_user_credential,
    delete_user_credential,
    get_all_user_credentials,
    get_user_credentials_by_keys,
    save_user_credentials_batch,
)

# 从 audit.py 导出
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
