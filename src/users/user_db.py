# -*- coding: utf-8 -*-
"""用户数据库模块 — 兼容层（重新导出所有公开函数）

原模块已按功能域拆分为子模块：
- _user_db_core   — 共享常量与 _conn()
- user_crud       — 用户 CRUD
- role_rbac       — 角色与 RBAC
- project_auth    — 项目授权与版本管理
- audit_logs      — 操作日志与配置历史
- credentials     — 用户凭证管理
- documents       — 文档版本与截图管理

所有原有导入路径保持不变。
"""

# 共享核心（常量 + _conn）
from src.users._user_db_core import (
    _conn,
    ALLOWED_ROLES,
    ACTION_LOG_RETENTION_DAYS,
    ACTION_LOG_MAX_ROWS,
    PROJECT_LEVEL_HIERARCHY,
)

# 用户 CRUD
from src.users.user_crud import (
    create_user,
    list_users,
    list_login_users,
    get_user_by_id,
    update_user,
    delete_user,
    get_user_last_project,
    set_user_last_project,
)

# 角色与 RBAC
from src.users.role_rbac import (
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
    get_user_roles,
    assign_role_to_user,
    remove_role_from_user,
    set_user_roles_bulk,
)

# 项目授权与版本管理
from src.users.project_auth import (
    assign_project,
    revoke_project,
    get_user_projects,
    can_view,
    is_admin,
    is_project_admin,
    is_project_member,
    get_user_system_role,
    get_project_level,
    create_project_version,
    activate_project_version,
    get_active_version,
    get_active_version_label,
    get_project_versions,
    update_project_version,
    archive_project_version,
)

# 操作日志与配置历史
from src.users.audit_logs import (
    log_action,
    record_config_change,
    get_config_history,
    rollback_config,
)

# 用户凭证管理
from src.users.credentials import (
    get_user_credential,
    set_user_credential,
    save_user_credentials_batch,
    delete_user_credential,
    get_all_user_credentials,
    get_user_credentials_by_keys,
)

# 文档版本与截图管理
from src.users.documents import (
    record_document_version,
    get_document_versions,
    record_source_document,
    get_source_document,
    save_screenshot,
    get_screenshot,
    get_screenshots_by_intent,
)
