# -*- coding: utf-8 -*-
"""权限管理"""

from src.users._user_db_core import _conn, _log, ALLOWED_ROLES


def get_role_permissions(role_id: str) -> dict[str, bool]:
    """获取某角色的权限字典 {permission_id: granted}。
    ★ 使用 role_id 列（与 schema.sql 一致）。
    """
    c = _conn()
    try:
        role = c.execute("SELECT * FROM roles WHERE id=%s", (role_id,)).fetchone()
        if not role:
            return {}
        from src.models.permission import PERMISSION_DEFINITIONS

        if role["id"] == "admin" or role["name"] in ("admin", "管理员"):
            return {k: True for k in PERMISSION_DEFINITIONS}
        granted = {
            r["permission"]
            for r in c.execute(
                "SELECT permission FROM role_permissions WHERE role_id=%s", (role_id,)
            ).fetchall()
        }
        return {k: (k in granted) for k in PERMISSION_DEFINITIONS}
    except Exception as e:
        _log.error("[Manager] get_role_permissions 异常: %s", e)
        return {}
    finally:

        c.close()


def set_role_permission(role_id: str, permission: str, granted: bool = True) -> bool:
    """授予或撤销角色的某个功能权限。
    ★ 使用 role_id 列（与 schema.sql 一致）。
    """
    c = _conn()
    try:
        role = c.execute("SELECT * FROM roles WHERE id=%s", (role_id,)).fetchone()
        if not role:
            return False
        if granted:
            c.execute(
                "REPLACE INTO role_permissions(role_id, permission) VALUES(%s,%s)",
                (role_id, permission),
            )
        else:
            c.execute(
                "DELETE FROM role_permissions WHERE role_id=%s AND permission=%s",
                (role_id, permission),
            )
        c.commit()
        return True
    except Exception as e:
        _log.error("[Manager] set_role_permission 异常: %s", e)
        return False
    finally:

        c.close()


def set_role_permissions_bulk(role_id: str, permissions: dict[str, bool]) -> bool:
    """批量设置角色的权限（原子操作）。
    ★ 使用 role_id 列（与 schema.sql 一致）。
    """
    c = _conn()
    try:
        role = c.execute("SELECT * FROM roles WHERE id=%s", (role_id,)).fetchone()
        if not role:
            return False
        c.execute("DELETE FROM role_permissions WHERE role_id=%s", (role_id,))
        for perm, granted in permissions.items():
            if granted:
                c.execute(
                    "INSERT IGNORE INTO role_permissions(role_id, permission) VALUES(%s,%s)",
                    (role_id, perm),
                )
        c.commit()
        return True
    except Exception as e:
        _log.error("[Manager] set_role_permissions_bulk 异常: %s", e)
        return False
    finally:

        c.close()


def get_role_projects(role_id: str) -> list[dict]:
    """获取某角色可访问的项目列表（含 level）。
    v3: role_projects 表已删除，项目成员关系改为 project_members 表（按用户绑定，非按角色）。
    此函数保留为空实现，供旧API兼容，条目7.2将删除前端项目Tab后不再调用。
    """
    return []


def get_role_project_names(role_id: str) -> list[str]:
    """v3: role_projects 已删除，返回空列表。"""
    return []


def set_role_project(
    role_id: str, project: str, granted: bool = True, level: str = "member"
) -> bool:
    """v3: role_projects 已删除，此函数为 no-op，始终返回 True。"""
    return True


def set_role_projects_bulk(role_id: str, projects: dict[str, bool]) -> bool:
    """v3: role_projects 已删除，此函数为 no-op，始终返回 True。"""
    return True


def get_user_roles(user_id: str) -> list[dict]:
    """获取某用户的所有角色。"""
    c = None
    try:
        c = _conn()
        rows = c.execute(
            "SELECT r.* FROM roles r JOIN user_roles ur ON r.id = ur.role_id "
            "WHERE ur.user_id = %s ORDER BY r.id",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    finally:
        if c:
            c.close()


def assign_role_to_user(user_id: str, role_id: str) -> bool:
    """给用户分配角色。"""
    c = _conn()
    try:
        c.execute(
            "INSERT IGNORE INTO user_roles(user_id, role_id) VALUES(%s,%s)",
            (user_id, role_id),
        )
        c.commit()
        from src.users.project_auth import invalidate_role_cache

        invalidate_role_cache(user_id)
        return True
    except Exception as e:
        _log.error(
            "[Manager] assign_role_to_user 异常: user_id=%s, role_id=%s, err=%s",
            user_id,
            role_id,
            e,
        )
        return False
    finally:

        c.close()


def remove_role_from_user(user_id: str, role_id: str) -> bool:
    """撤销用户的某个角色。检查至少保留一个角色。"""
    c = _conn()
    try:
        count = c.execute(
            "SELECT COUNT(*) as n FROM user_roles WHERE user_id=%s", (user_id,)
        ).fetchone()["n"]
        if count <= 1:
            return False
        c.execute(
            "DELETE FROM user_roles WHERE user_id=%s AND role_id=%s", (user_id, role_id)
        )
        c.commit()
        from src.users.project_auth import invalidate_role_cache

        invalidate_role_cache(user_id)
        return True
    except Exception as e:
        _log.error("[Manager] remove_role_from_user 异常: %s", e)
        return False
    finally:

        c.close()


def set_user_roles_bulk(user_id: str, role_ids: list[str]) -> bool:
    """批量设置用户的角色列表（原子操作）。至少保留一个角色。
    v3: 校验 role_ids 在 ALLOWED_ROLES 范围内；防止最后一位系统管理员被移除。
    """
    if not role_ids:
        return False
    # 校验角色ID合法性
    for rid in role_ids:
        if rid not in ALLOWED_ROLES:
            _log.error("[Manager] set_user_roles_bulk: 非法角色ID '%s'", rid)
            return False
    c = _conn()
    try:
        # 防止最后一位系统管理员被移除admin角色
        if "admin" not in role_ids:
            is_target_admin = c.execute(
                "SELECT 1 FROM user_roles ur JOIN roles r ON ur.role_id=r.id "
                "WHERE r.id='admin' AND ur.user_id=%s",
                (user_id,),
            ).fetchone()
            if is_target_admin:
                admin_count = c.execute(
                    "SELECT COUNT(*) n FROM user_roles ur JOIN roles r ON ur.role_id=r.id "
                    "WHERE r.id='admin'"
                ).fetchone()["n"]
                if admin_count <= 1:
                    _log.info(
                        "[Manager] 拒绝移除 %s 的admin角色：最后一位系统管理员", user_id
                    )
                    return False
        c.execute("DELETE FROM user_roles WHERE user_id=%s", (user_id,))
        for rid in role_ids:
            c.execute(
                "INSERT IGNORE INTO user_roles(user_id, role_id) VALUES(%s,%s)",
                (user_id, rid),
            )
        c.commit()
        from src.users.project_auth import invalidate_role_cache

        invalidate_role_cache(user_id)
        return True
    except Exception as e:
        _log.error("[Manager] set_user_roles_bulk 异常: %s", e)
        return False
    finally:

        c.close()
