# -*- coding: utf-8 -*-
from __future__ import annotations

"""用户 CRUD 模块"""
import re as _re
from datetime import datetime

from src.users._user_db_core import _conn, _log


def create_user(
    user_id: str, name: str, password: str, role: str = "user"
) -> dict | None:
    """创建用户。返回用户字典，失败返回 None。"""
    from src.users.auth import validate_password_strength, _hash

    ok_pw, err_pw = validate_password_strength(password)
    if not ok_pw:
        _log.info(f"[Manager] 创建用户 {user_id} 失败: {err_pw}")
        return None
    if not _re.match(r"^[a-zA-Z0-9_\-一-鿿]+$", user_id):
        _log.info(f"[Manager] 创建用户 {user_id} 失败: 用户名格式非法")
        return None
    if len(name) < 1:
        _log.info("[Manager] 创建用户失败: 显示名为空")
        return None
    c = _conn()
    try:
        if c.execute("SELECT 1 FROM users WHERE id=%s", (user_id,)).fetchone():
            return None
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "INSERT INTO users(id, name, password, created_at) VALUES(%s,%s,%s,%s)",
            (user_id, name, _hash(password), now),
        )
        # v3: 使用新的 ALLOWED_ROLES，默认 project_member
        from src.users._user_db_core import ALLOWED_ROLES

        role_name = role if role in ALLOWED_ROLES else "project_member"
        role_row = c.execute(
            "SELECT id FROM roles WHERE id=%s", (role_name,)
        ).fetchone()
        if role_row:
            c.execute(
                "INSERT IGNORE INTO user_roles(user_id, role_id) VALUES(%s,%s)",
                (user_id, role_row["id"]),
            )
        c.commit()
        return dict(c.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone())
    except Exception as e:
        _log.error("[Manager] create_user 异常: %s", e)
        return None
    finally:

        c.close()


def list_login_users() -> list[dict]:
    """★ 登录弹窗下拉专用 — 只查 users 表，不 JOIN user_roles。

    登录下拉只需要 id + name，根本不需要角色信息。
    原 list_users() 走 LEFT JOIN user_roles 笛卡尔展开，导致多角色用户在
    登录下拉出现多次（如 BOBO 有 user + proj_BOBO_admin 两个角色 → 出现 2 次）。
    本函数彻底绕开 user_roles，从根上保证一个用户一行。
    """
    c = None
    try:
        c = _conn()
        rows = c.execute(
            "SELECT id, name, status FROM users ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if c:
            c.close()


def list_users() -> list[dict]:
    """列出所有用户（含主角色 + 全部角色列表），供用户管理面板使用。

    ★ user_roles 允许一对多（基础 role + 项目专属 role），
      GROUP BY u.id + GROUP_CONCAT 聚合保证每用户一行。
    """
    c = None
    try:
        c = _conn()
        rows = c.execute(
            "SELECT u.id, u.name, u.last_login, u.created_at, u.updated_at, "
            # ★ 主角色优先级：admin > user > viewer。映射成 3/2/1 取 MAX 避免字典序问题
            "COALESCE((SELECT CASE WHEN r2.id = 'admin' THEN 3 "
            "                         WHEN r2.id = 'project_admin' THEN 2 "
            "                         WHEN r2.id = 'project_member' THEN 1 "
            "                         ELSE 0 END "
            "            FROM user_roles ur2 JOIN roles r2 ON ur2.role_id = r2.id "
            "            WHERE ur2.user_id = u.id "
            "            ORDER BY 1 DESC LIMIT 1), 2) AS role_rank, "
            "GROUP_CONCAT(DISTINCT r.name ORDER BY r.name SEPARATOR ',') AS roles_csv "
            "FROM users u "
            "LEFT JOIN user_roles ur ON u.id = ur.user_id "
            "LEFT JOIN roles r ON ur.role_id = r.id "
            "GROUP BY u.id "
            "ORDER BY u.created_at"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            rank = d.pop("role_rank") or 0
            d["role"] = {3: "admin", 2: "project_admin", 1: "project_member"}.get(
                rank, "user"
            )
            d["roles"] = [x for x in (d.pop("roles_csv") or "").split(",") if x]
            out.append(d)
        return out
    finally:
        if c:
            c.close()


def get_user_by_id(user_id: str) -> dict | None:
    """按 ID 查询单个用户（含主角色 + 全部角色列表）。

    ★ 与 list_users 保持一致的聚合语义（见 list_users 注释）。
    """
    if not user_id:
        return None
    c = _conn()
    try:
        row = c.execute(
            "SELECT u.id, u.name, u.last_login, u.created_at, u.updated_at, "
            "COALESCE((SELECT CASE WHEN r2.id = 'admin' THEN 3 "
            "                         WHEN r2.id = 'project_admin' THEN 2 "
            "                         WHEN r2.id = 'project_member' THEN 1 "
            "                         ELSE 0 END "
            "            FROM user_roles ur2 JOIN roles r2 ON ur2.role_id = r2.id "
            "            WHERE ur2.user_id = u.id "
            "            ORDER BY 1 DESC LIMIT 1), 2) AS role_rank, "
            "GROUP_CONCAT(DISTINCT r.name ORDER BY r.name SEPARATOR ',') AS roles_csv "
            "FROM users u "
            "LEFT JOIN user_roles ur ON u.id = ur.user_id "
            "LEFT JOIN roles r ON ur.role_id = r.id "
            "WHERE u.id=%s "
            "GROUP BY u.id",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        rank = d.pop("role_rank") or 0
        d["role"] = {3: "admin", 2: "project_admin", 1: "project_member"}.get(
            rank, "user"
        )
        d["roles"] = [x for x in (d.pop("roles_csv") or "").split(",") if x]
        return d
    finally:
        c.close()


def update_user(user_id: str, **fields) -> tuple:
    """更新用户字段。返回 (ok: bool, err_msg: str)。支持: name, password。角色变更请使用 set_user_role()。"""
    from src.users.auth import validate_password_strength, _hash

    updates = {}
    for k, v in fields.items():
        if k == "password":
            ok_pw, err_pw = validate_password_strength(v)
            if not ok_pw:
                _log.info(f"[Manager] 更新密码失败: {err_pw}")
                return False, err_pw
            updates[k] = _hash(v)
        elif k == "name":
            updates[k] = v
    if not updates:
        return False, "没有可更新的字段"
    c = _conn()
    try:
        from src.core.db_security import validate_column_names

        validate_column_names(list(updates.keys()))
        validate_column_names(list(updates.keys()))
        ok = (
            c.execute(
                f"UPDATE users SET {', '.join(f'{k}=%s' for k in updates)} WHERE id=%s",
                list(updates.values()) + [user_id],
            ).rowcount
            > 0
        )
        c.commit()
        if ok:
            return True, ""
        return False, "用户不存在"
    except Exception as e:
        _log.error("[Manager] update_user 异常: %s", e)
        return False, "数据库更新失败"
    finally:
        c.close()


def get_user_last_project(user_id: str) -> str:
    """读取用户上次选择的项目（用于登录后自动恢复，避免每次弹回项目列表）。

    无记录 / 列不存在 / 异常均安全回退为空串。
    """
    if not user_id:
        return ""
    c = _conn()
    try:
        row = c.execute(
            "SELECT last_project FROM users WHERE id=%s", (str(user_id),)
        ).fetchone()
        return (row["last_project"] or "") if row else ""
    except Exception as e:
        _log.warning("[Manager] get_user_last_project 异常: %s", str(e)[:120])
        return ""
    finally:
        c.close()


def set_user_last_project(user_id: str, project: str = "") -> bool:
    """持久化用户上次选择的项目（成功后登录恢复 / 切换项目时写回）。

    失败不抛出，静默返回 False（不影响主流程）。
    """
    if not user_id:
        return False
    c = _conn()
    try:
        c.execute(
            "UPDATE users SET last_project=%s WHERE id=%s",
            (project or "", str(user_id)),
        )
        c.commit()
        return True
    except Exception as e:
        _log.warning("[Manager] set_user_last_project 异常: %s", str(e)[:120])
        return False
    finally:
        c.close()


def delete_user(user_id: str) -> bool:
    """删除用户及关联数据。不允许删除最后一位管理员。"""
    c = _conn()
    try:
        if c.execute(
            "SELECT 1 FROM user_roles ur JOIN roles r ON ur.role_id=r.id "
            "WHERE ur.user_id=%s AND (r.name='admin' OR r.name='管理员')",
            (user_id,),
        ).fetchone():
            n = c.execute(
                "SELECT COUNT(*) n FROM user_roles ur JOIN roles r ON ur.role_id=r.id "
                "WHERE r.name='admin' OR r.name='管理员'"
            ).fetchone()["n"]
            if n <= 1:
                _log.info("[Manager] 拒绝删除 %s：最后一位管理员", user_id)
                return False
        c.execute("DELETE FROM executions WHERE user_id=%s", (user_id,))
        ok = c.execute("DELETE FROM users WHERE id=%s", (user_id,)).rowcount > 0
        c.commit()
        return ok
    except Exception as e:
        _log.error("[Manager] delete_user 异常: %s", e)
        return False
    finally:
        c.close()
