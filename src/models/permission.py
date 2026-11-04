# -*- coding: utf-8 -*-
"""
功能权限管理系统（三层权限模型）
===================================
简化设计：系统级 + 项目级 + 个人级，三层正交不重叠。

使用方式：
    from src.models.permission import has_permission, require_permission, has_project_permission

    # 后端 API 校验（系统级）
    @require_permission("execution.run")
    def api_run_step():
        ...

    # 项目级权限校验
    if not has_project_permission(uid, project, "execution.run"):
        return jsonify({"error": "无权在此项目执行"}), 403
"""

import json
from typing import List, Set, Optional, Dict
from src.models.base import get_conn, safe_add_column
import logging
_log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 权限注册表（系统级功能权限 — 仅控制全局功能开关）
# ═══════════════════════════════════════════════════════════════

PERMISSION_DEFINITIONS = {
    # ── 系统管理（仅系统管理员拥有） ──
    "user.manage":        "管理用户（查看/创建/编辑/删除）",
    "user.role.set":      "设置用户角色（提权/降权）",
    "system.settings":    "修改系统设置",

    # ── 项目管理（创建/删除项目权限） ──
    "project.create":     "创建新项目",
    "project.delete":     "删除/归档项目",

    # ── 执行控制（能否启动流水线） ──
    "execution.run":      "启动测试执行",
    "execution.stop":     "停止测试执行",

    # ── 文档中心 ──
    "docs.view":          "查看文档中心",
    "docs.export":        "导出报告",

    # ── AI 健康度 ──
    "health.view":        "查看 AI 健康度仪表盘",

    # ── 用例上传（Step3 用例准备）──
    # ★ 用例绑定专项(2026-08): 独立于通用 file.upload，区分"需求文档上传"与"测试用例上传执行"
    # ★ 全局修复(2026-09-01): 名称简化为"下载用例模板"，避免"下载用例/上传模板"歧义；
    #   模板由系统定义仅可下载、不可上传。
    "case.template":      "下载用例模板（模板由系统定义，仅供下载）",
    "case.upload":        "上传并执行测试用例（需绑定被测系统并校验版本匹配）",
}

# 仅系统管理员可拥有的权限点（提权/用户管理/系统设置/项目删除）。
# 来源：schema.sql 中 project_admin 种子注释明确排除的“系统管理员独有权限”。
SYSTEM_ADMIN_ONLY_PERMISSIONS = {
    "user.manage",
    "user.role.set",
    "user.project.assign",
    "system.settings",
    "project.delete",
}

# 角色默认权限（系统级）
DEFAULT_ROLE_PERMISSIONS = {
    "admin": set(PERMISSION_DEFINITIONS.keys()),  # 管理员拥有全部系统权限
    "user": {
        "execution.run",
        "execution.stop",
        "docs.view",
        "docs.export",
        "health.view",
        "case.template",
        "case.upload",
    },
    "viewer": {
        "docs.view",
        "health.view",
    },
}


# ═══════════════════════════════════════════════════════════════
# 数据库表初始化（注册为迁移）
# ═══════════════════════════════════════════════════════════════

def _init_permission_tables(c):
    """（已废弃 — schema.sql 已创建 role_permissions 表）"""
    pass


def _seed_default_permissions(c):
    """种子默认角色权限（仅当 role_permissions 为空时）。"""
    count = c.execute("SELECT COUNT(*) as n FROM role_permissions").fetchone()
    if count and count["n"] > 0:
        return
    for role_id, perms in DEFAULT_ROLE_PERMISSIONS.items():
        for perm in perms:
            c.execute(
                "INSERT IGNORE INTO role_permissions(role_id, permission, granted) VALUES(%s,%s,%s)",
                (role_id, perm, 1)
            )
    c.commit()
    _log.info(f"[Permission] 已种子默认权限: admin={len(DEFAULT_ROLE_PERMISSIONS['admin'])} 项, "
          f"user={len(DEFAULT_ROLE_PERMISSIONS['user'])} 项, "
          f"viewer={len(DEFAULT_ROLE_PERMISSIONS['viewer'])} 项")


# ═══════════════════════════════════════════════════════════════
# 权限查询 API（RBAC 跨角色聚合）
# ═══════════════════════════════════════════════════════════════

def get_user_permissions(user_id: str) -> Set[str]:
    """
    获取用户的所有系统级权限（跨所有角色的并集）。
    ★ 管理员角色 → 全部权限。
    """
    if not user_id:
        return set()
    c = get_conn()
    try:
        # 检查是否拥有管理员角色
        admin_check = c.execute(
            "SELECT 1 FROM user_roles ur JOIN roles r ON ur.role_id=r.id "
            "WHERE ur.user_id=%s AND (r.name='管理员' OR r.name='admin')", (user_id,)
        ).fetchone()
        if admin_check:
            return set(PERMISSION_DEFINITIONS.keys())

        # 聚合所有角色的权限
        rows = c.execute("""
            SELECT DISTINCT rp.permission
            FROM role_permissions rp
            JOIN user_roles ur ON rp.role_id = ur.role_id
            WHERE ur.user_id = %s AND rp.granted = 1
        """, (user_id,)).fetchall()

        return {r["permission"] for r in rows}
    finally:
        c.close()


def has_permission(user_id: str, permission: str) -> bool:
    """检查用户是否拥有指定系统级权限。"""
    if not user_id or not permission:
        return False
    return permission in get_user_permissions(user_id)


def has_any_permission(user_id: str, permissions: List[str]) -> bool:
    """检查用户是否拥有任意一个系统级权限。"""
    if not user_id or not permissions:
        return False
    user_perms = get_user_permissions(user_id)
    return any(p in user_perms for p in permissions)


def check_admin_or_permission(permission: str, project: str = None):
    """检查当前登录用户是否为管理员或拥有指定系统级权限。
    返回 (ok: bool, error_message: str)。ok=True 表示放行。

    错误消息约定：
      - 以 "请先登录" 开头  -> 调用端应返回 HTTP 401（未认证）
      - 其他拒绝消息       -> 调用端应返回 HTTP 403（已认证但无权限）

    ★ 2026-08-04 适配：project 为可选参数（当前 HEAD 误删本文件后由历史版本
    恢复，但多处路由调用传 project=）；系统级权限检查忽略 project，保持兼容。
    """
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin
    uid = get_current_user()
    if not uid:
        return False, "请先登录"
    if is_admin(uid) or has_permission(uid, permission):
        return True, ""
    return False, f"无权执行此操作（需要权限: {permission}）"


def get_user_role_projects(user_id: str) -> Set[str]:
    """获取用户通过角色获得的所有项目访问权。"""
    if not user_id:
        return set()
    from src.users.manager import is_admin
    c = get_conn()
    try:
        # admin → 全部项目（从数据库读取）
        if is_admin(user_id):
            _rows = c.execute("SELECT name FROM projects WHERE status='active'").fetchall()
            return {r["name"] for r in _rows}
        rows = c.execute("""
            SELECT DISTINCT rp.project FROM role_projects rp
            JOIN user_roles ur ON rp.role_id = ur.role_id
            WHERE ur.user_id = %s
        """, (user_id,)).fetchall()
        return {r["project"] for r in rows}
    finally:
        c.close()


def set_role_permission_by_name(role_id: str, permission: str, granted: bool = True):
    """授予或撤销某个角色（按ID）的某个权限。"""
    c = get_conn()
    try:
        if granted:
            c.execute(
                "REPLACE INTO role_permissions(role_id, permission, granted) VALUES(%s,%s,%s)",
                (role_id, permission, 1)
            )
        else:
            c.execute(
                "DELETE FROM role_permissions WHERE role_id=%s AND permission=%s",
                (role_id, permission)
            )
        c.commit()
    finally:
        c.close()


def get_role_permissions_by_name(role_name: str) -> List[Dict]:
    """获取某角色（按ID）的完整权限列表。"""
    all_perms = PERMISSION_DEFINITIONS
    c = get_conn()
    try:
        rows = c.execute(
            "SELECT permission, granted FROM role_permissions WHERE role_id=%s",
            (role_name,)
        ).fetchall()
        granted = {r["permission"] for r in rows if r.get("granted", 1)}
        result = []
        for perm_id, desc in all_perms.items():
            result.append({
                "id": perm_id,
                "description": desc,
                "granted": perm_id in granted,
            })
        return result
    finally:
        c.close()


def get_all_permissions() -> List[Dict]:
    """获取权限注册表中的所有权限定义。"""
    return [
        {"id": perm_id, "description": desc}
        for perm_id, desc in PERMISSION_DEFINITIONS.items()
    ]


# ═══════════════════════════════════════════════════════════════
# Flask 装饰器（后端 API 系统级权限校验）
# ═══════════════════════════════════════════════════════════════

def require_permission(permission: str):
    """
    Flask 路由装饰器：检查当前用户是否拥有指定系统级权限。
    无权限时返回 403 JSON。
    """
    import functools
    from flask import jsonify

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            from src.core.config_loader import get_current_user
            uid = get_current_user()
            if not uid:
                return jsonify({"error": "请先登录"}), 401
            if not has_permission(uid, permission):
                return jsonify({"error": "无权执行此操作（需要权限: %s）" % permission}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════
# 项目级权限（三层正交设计：系统级 + 项目级 + 个人级）
# ═══════════════════════════════════════════════════════════════

# 项目内权限级别对应的操作权限
# ★ 注意：项目级权限只控制"在这个项目里能做什么"，与系统级权限正交
# ★ BUG-2(2026-08-05): get_project_level 对 admin 返回 "owner"（project_auth.py:287-296），
#   但原 PROJECT_LEVEL_PERMISSIONS 只有 admin/member/viewer 键 → has_project_permission
#   对 admin 取到空集 → API 层 403"无权切换版本"。修复：owner 与 admin 共用同一全量集，
#   不破坏 admin/member/viewer 语义。
_PROJECT_ADMIN_LEVEL_PERMISSIONS = {
    "project.config.view",
    "project.config.edit",
    "project.config.history",
    "project.config.rollback",
    "project.member.manage",
    "project.version.create",
    "project.version.edit",
    "project.version.archive",
    "project.version.delete",
    "file.upload",
    "file.download",
    "file.clear",
    "datasource.figma.refresh",
    "datasource.yapi.refresh",
    "datasource.confluence.refresh",
    "datasource.db.test",
    "datasource.bg.upload",
    "execution.run",
    "execution.stop",
    "execution.retry",
    "execution.control",
    "execution.step1",
    "execution.step2",
    "execution.step3",
    "execution.step4",
    "execution.step5",
    "docs.view",
    "docs.edit",
    "docs.export",
    "system.rag.rebuild",
}
PROJECT_LEVEL_PERMISSIONS = {
    "owner": _PROJECT_ADMIN_LEVEL_PERMISSIONS,
    "admin": _PROJECT_ADMIN_LEVEL_PERMISSIONS,
    "member": {
        "project.config.view",
        "file.download",
        # ★ 2026-08-27 产品决策：执行者(member)只有被分配项目的执行权限(step4/5)，
        #   不授予 execution.run（一键执行=全流程 Step1-5，会绕过 step1-3 权限限制）。
        #   execution.stop 保留（执行者可停止其项目中的运行）。
        "execution.stop",
        "execution.step4",
        "execution.step5",
        "docs.view",
        "docs.export",
        "health.view",
    },
    "viewer": {
        "project.config.view",
        "docs.view",
        "health.view",
    },
}


def has_project_permission(user_id: str, project: str, permission: str) -> bool:
    """
    检查用户是否在指定项目内有某项操作权限。
    ★ 简化设计：系统级权限和项目级权限独立判断，不互相覆盖。
    
    判断逻辑：
    1. 用户是否属于该项目（通过 role_projects 或 admin 角色）
    2. 用户在项目中的级别是否包含该权限
    
    注意：系统级权限（如 user.manage, system.settings）不在项目级判断范围内。
    """
    if not user_id or not project or not permission:
        return False
    
    # 系统级权限（如 user.manage, system.settings）不走项目级判断
    if permission in PERMISSION_DEFINITIONS:
        return has_permission(user_id, permission)
    
    # 检查项目内级别
    from src.users.manager import get_project_level
    level = get_project_level(user_id, project)
    if not level:
        return False
    return permission in PROJECT_LEVEL_PERMISSIONS.get(level, set())


def can_execute_in_project(user_id: str, project: str) -> tuple:
    """
    检查用户是否可以在指定项目中执行流水线。
    组合判断：系统级 execution.run + 项目级 member/admin。
    
    返回 (ok: bool, error_message: str)
    """
    from src.core.config_loader import get_current_user
    from src.users.manager import is_admin, get_project_level
    
    uid = get_current_user()
    if not uid:
        return False, "请先登录"
    
    # 1. 系统级权限检查：是否有 execution.run
    if not has_permission(uid, "execution.run"):
        return False, "无权执行测试（需要系统权限: execution.run）"
    
    # 2. 项目级权限检查：是否属于该项目且级别 >= member
    level = get_project_level(uid, project)
    if not level:
        return False, "无权访问此项目"
    
    if level == "viewer":
        return False, "只读用户不能执行测试"
    
    return True, ""
