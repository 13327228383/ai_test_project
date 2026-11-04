# -*- coding: utf-8 -*-
"""用户角色管理"""

from src.users._user_db_core import _conn, _log, ALLOWED_ROLES


def set_user_role(user_id: str, new_role: str) -> bool:
    """设置用户角色。使用 SELECT FOR UPDATE 防止并发降级导致零管理员。"""
    if new_role not in ALLOWED_ROLES:
        return False
    c = _conn()
    try:
        if not c.execute("SELECT id FROM users WHERE id=%s", (user_id,)).fetchone():
            return False
        role_row = c.execute("SELECT id FROM roles WHERE id=%s", (new_role,)).fetchone()
        if not role_row:
            _log.error(
                "[Manager] set_user_role: 角色 '%s' 在 roles 表中不存在", new_role
            )
            return False
        # 防止最后一位系统管理员被降级（基于 r.id='admin'）
        if new_role != "admin":
            admin_ids = {
                r["user_id"]
                for r in c.execute(
                    "SELECT ur.user_id FROM user_roles ur JOIN roles r ON ur.role_id=r.id "
                    "WHERE r.id='admin' FOR UPDATE"
                ).fetchall()
            }
            if user_id in admin_ids and len(admin_ids) <= 1:
                _log.info("[Manager] 拒绝降级 %s：最后一位系统管理员", user_id)
                return False
        c.execute("DELETE FROM user_roles WHERE user_id=%s", (user_id,))
        c.execute(
            "INSERT IGNORE INTO user_roles(user_id, role_id) VALUES(%s,%s)",
            (user_id, role_row["id"]),
        )
        c.commit()
        from src.users.project_auth import invalidate_role_cache

        invalidate_role_cache(user_id)
        from src.users.user_db import log_action

        log_action(user_id, "role-change", scope_type="system", detail=f"→ {new_role}")
        return True
    except Exception as e:
        c.rollback()
        _log.error("[Manager] set_user_role 异常: %s", e)
        return False
    finally:

        c.close()


def promote_to_admin(user_id: str) -> bool:
    return set_user_role(user_id, "admin")


def promote_to_project_admin(user_id: str) -> bool:
    """将用户提升为项目管理员。"""
    return set_user_role(user_id, "project_admin")


def set_as_project_member(user_id: str) -> bool:
    """将用户设置为项目成员。"""
    return set_user_role(user_id, "project_member")


def demote_from_admin(user_id: str) -> bool:
    """将系统管理员降级为项目管理员。"""
    return set_user_role(user_id, "project_admin")


def get_admin_count() -> int:
    """获取当前系统管理员数量（基于 r.id='admin'）。"""
    c = None
    try:
        c = _conn()
        n = c.execute(
            "SELECT COUNT(*) n FROM user_roles ur JOIN roles r ON ur.role_id=r.id "
            "WHERE r.id='admin'"
        ).fetchone()["n"]
        return n

    finally:
        if c:
            c.close()
