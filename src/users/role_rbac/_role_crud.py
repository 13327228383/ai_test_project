# -*- coding: utf-8 -*-
from __future__ import annotations

"""角色 CRUD"""
from datetime import datetime

from src.users._user_db_core import _conn, _log


def create_role(name: str, description: str = "", is_system: int = 0) -> str | None:
    """创建新角色，返回 role_id（字符串主键）。失败返回 None。

    ★ roles.id 是 VARCHAR(64) 业务主键（如 admin/user/viewer），非自增。
      自定义角色 id 由 name 生成 slug 并去重，避免与系统角色冲突。
    """
    if not name or not name.strip():
        return None
    import re

    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_").lower() or "role"
    c = _conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        # ★ 生成不冲突的 role_id（base / base_2 / base_3 ...）
        rid = base
        suffix = 1
        while c.execute("SELECT 1 FROM roles WHERE id=%s", (rid,)).fetchone():
            suffix += 1
            rid = f"{base}_{suffix}"
        c.execute(
            "INSERT INTO roles(id, name, description, is_system, created_at) VALUES(%s,%s,%s,%s,%s)",
            (rid, name.strip(), description, is_system, now),
        )
        c.commit()
    except Exception as e:
        _log.info(f"[Manager] 创建角色失败: {e}")
        rid = None
    finally:

        c.close()
    return rid


def update_role(role_id: int, **fields) -> bool:
    """更新角色（name, description）。系统角色不可改名。"""
    c = _conn()
    try:
        role = c.execute("SELECT * FROM roles WHERE id=%s", (role_id,)).fetchone()
        if not role:
            return False
        updates = {}
        if "name" in fields and not role["is_system"]:
            updates["name"] = fields["name"].strip()
        if "description" in fields:
            updates["description"] = fields["description"]
        if not updates:
            return True
        from src.core.db_manager import validate_column_names

        validate_column_names(list(updates.keys()))
        validate_column_names(list(updates.keys()))
        c.execute(
            f"UPDATE roles SET {', '.join(f'{k}=%s' for k in updates)} WHERE id=%s",
            list(updates.values()) + [role_id],
        )
        c.commit()
        return True
    except Exception as e:
        _log.error("[Manager] update_role 异常: %s", e)
        return False
    finally:

        c.close()


def delete_role(role_id: str) -> bool:
    """删除角色（系统角色不可删）。级联清理关联数据。"""
    c = _conn()
    try:
        role = c.execute("SELECT * FROM roles WHERE id=%s", (role_id,)).fetchone()
        if not role or role["is_system"]:
            return False
        # v3: role_projects 表已删除，只需清理 user_roles + role_permissions + roles
        c.execute("DELETE FROM user_roles WHERE role_id=%s", (role_id,))
        c.execute("DELETE FROM role_permissions WHERE role_id=%s", (role_id,))
        c.execute("DELETE FROM roles WHERE id=%s", (role_id,))
        c.commit()
        return True
    except Exception as e:
        _log.error("[Manager] delete_role 异常: %s", e)
        return False
    finally:

        c.close()


def get_all_roles() -> list[dict]:
    """获取所有角色列表（含用户数量统计）。"""
    c = None
    try:
        c = _conn()
        rows = c.execute(
            "SELECT r.*, COUNT(ur.user_id) as user_count FROM roles r "
            "LEFT JOIN user_roles ur ON r.id = ur.role_id GROUP BY r.id "
            "ORDER BY r.is_system DESC, r.id ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    finally:
        if c:
            c.close()


def get_role(role_id: str) -> dict | None:
    """获取单个角色详情。"""
    c = None
    try:
        c = _conn()
        row = c.execute("SELECT * FROM roles WHERE id=%s", (role_id,)).fetchone()
        return dict(row) if row else None

    finally:
        if c:
            c.close()
