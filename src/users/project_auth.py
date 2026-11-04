# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard
from src.core.known_tables import KNOWN_LOGICAL_TABLES as _logical_tables  # noqa: F401  词表唯一源

"""项目授权与版本管理模块（两层权限模型 v3 — project_members 表）"""
from datetime import datetime

from src.users._user_db_core import _conn, _log
import logging

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 项目成员管理（v3 — project_members 表，owner/member 两级）
# ═══════════════════════════════════════════════════════════════


def assign_project(
    project: str, user_id: str, level: str = "owner", added_by: str = ""
) -> bool:
    """
    将用户绑定到项目的指定身份（两层模型 v3）。

    - level="owner": 项目所有者，同时确保拥有 project_admin 系统角色
    - level="member": 项目成员，同时确保拥有 project_member 系统角色
    - 旧值 "editor"/"viewer" 自动映射为 "member"

    返回 True 表示成功，False 表示失败。
    """
    if not project or not user_id:
        return False

    # 规范化 level
    if level not in ("owner", "member"):
        level = "member"

    c = _conn()
    try:
        # 0. ★ 单 owner 约束（自动降级旧 owner）：设新 owner 时，把项目现有其他
        #    owner 自动降级为 member，保证任一项目至多一个 owner。
        #    系统管理员/项目管理员通过调换管理员（transfer_owner）走同一路径。
        if level == "owner":
            row_old = c.execute(
                "SELECT user_id FROM project_members "
                "WHERE project=%s AND role='owner' AND user_id<>%s LIMIT 1",
                (project, user_id),
            ).fetchone()
            if row_old:
                old_owner = row_old["user_id"]
                c.execute(
                    "UPDATE project_members SET role='member' "
                    "WHERE project=%s AND user_id=%s AND role='owner'",
                    (project, old_owner),
                )
                _log.info(
                    "[assign_project] 自动降级旧 owner %s → member（项目 %s）",
                    old_owner,
                    project,
                )

        # 1. 插入/更新 project_members 记录
        c.execute(
            "INSERT INTO project_members(user_id, project, role, added_by) "
            "VALUES(%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE role=%s, added_by=%s",
            (user_id, project, level, added_by or user_id, level, added_by or user_id),
        )

        # 2. 确保用户拥有对应的系统角色
        sys_role = "project_admin" if level == "owner" else "project_member"
        c.execute(
            "INSERT IGNORE INTO user_roles(user_id, role_id) VALUES(%s,%s)",
            (user_id, sys_role),
        )

        c.commit()
        invalidate_role_cache(user_id)
        _log.info(
            "[assign_project] %s → 项目 %s (role=%s, sys_role=%s)",
            user_id,
            project,
            level,
            sys_role,
        )
        return True
    except Exception as e:
        _log.error("[assign_project] 失败: %s", e)
        try:
            c.rollback()
        except Exception as _e:
            _log.debug("commit: %s", _e, exc_info=True)
        return False
    finally:
        c.close()


def transfer_owner(project: str, new_owner: str, old_owner: str = "", by: str = "") -> bool:
    """
    调换项目管理员（单 owner 约束）。

    将 new_owner 设为项目 owner，并把当前 owner（old_owner 或自动探测到的）
    降级为 member，确保项目至多一个 owner。无旧 owner 时等价于 assign owner。

    仅供系统管理员/项目管理员（需 project.member.manage 或系统 admin）调用。
    """
    if not project or not new_owner:
        return False

    c = _conn()
    try:
        # 自动探测当前 owner（未显式传 old_owner 时）
        cur_owner = old_owner
        if not cur_owner:
            row = c.execute(
                "SELECT user_id FROM project_members WHERE project=%s AND role='owner' "
                "AND user_id<>%s LIMIT 1",
                (project, new_owner),
            ).fetchone()
            if row:
                cur_owner = row["user_id"]

        # 降级旧 owner 为 member（与新 owner 不同时）
        if cur_owner and cur_owner != new_owner:
            c.execute(
                "UPDATE project_members SET role='member' "
                "WHERE project=%s AND user_id=%s AND role='owner'",
                (project, cur_owner),
            )
            _log.info(
                "[transfer_owner] %s → %s 调换管理员（项目 %s，旧 owner %s）",
                by or "?",
                new_owner,
                project,
                cur_owner,
            )

        # 设新 owner
        c.execute(
            "INSERT INTO project_members(user_id, project, role, added_by) "
            "VALUES(%s,%s,'owner',%s) "
            "ON DUPLICATE KEY UPDATE role='owner', added_by=%s",
            (new_owner, project, by or new_owner, by or new_owner),
        )
        c.execute(
            "INSERT IGNORE INTO user_roles(user_id, role_id) VALUES(%s,'project_admin')",
            (new_owner,),
        )
        c.commit()
        invalidate_role_cache(cur_owner)
        invalidate_role_cache(new_owner)
        return True
    except Exception as e:
        _log.error("[transfer_owner] 失败: %s", e)
        try:
            c.rollback()
        except Exception as _e:
            _log.debug("transfer_owner 回滚异常: %s", _e, exc_info=True)
        return False
    finally:
        c.close()


def revoke_project(project: str, user_id: str, level: str = None) -> bool:
    """
    从项目中移除用户（两层模型 v3）。

    - level=None: 移除用户在该项目的所有记录
    - level=具体值: 仅移除该身份记录
    """
    if not project or not user_id:
        return False
    c = _conn()
    try:
        if level:
            c.execute(
                "DELETE FROM project_members WHERE user_id=%s AND project=%s AND role=%s",
                (user_id, project, level),
            )
        else:
            c.execute(
                "DELETE FROM project_members WHERE user_id=%s AND project=%s",
                (user_id, project),
            )
        c.commit()
        _log.info(
            "[revoke_project] %s 移出项目 %s (level=%s)",
            user_id,
            project,
            level or "ALL",
        )
        return True
    except Exception as e:
        _log.error("[revoke_project] 失败: %s", e)
        return False
    finally:
        c.close()


def cleanup_project_roles(project: str) -> bool:
    """
    删除项目的所有成员记录（项目被删除时调用）。
    project_members 通过外键 ON DELETE CASCADE 自动清理，此函数作为兜底。
    """
    if not project:
        return False
    c = _conn()
    try:
        c.execute("DELETE FROM project_members WHERE project=%s", (project,))
        c.commit()
        _log.info("[cleanup_project_roles] 已清理项目 %s 的所有成员记录", project)
        return True
    except Exception as e:
        _log.error("[cleanup_project_roles] 失败: %s", e)
        return False
    finally:
        c.close()


# ═══════════════════════════════════════════════════════════════
# ★ 性能优化：角色检查 30 秒 TTL 缓存
# 用户角色不频繁变更（仅管理员操作角色管理时变更），缓存可大幅减少
# _require_login 中间件每请求的 is_admin DB 查询（并发场景下从 N 次 DB
# 查询降到 1 次/30s）。角色变更时调用 invalidate_role_cache() 主动失效。
# ═══════════════════════════════════════════════════════════════
import time as _time
import threading as _threading

_ROLE_CACHE_TTL = 30  # 秒
_role_cache: dict[str, tuple[float, bool]] = {}  # {key: (timestamp, result)}
_role_cache_lock = _threading.Lock()


def _role_cache_get(key: str) -> tuple[bool, bool]:
    """返回 (has_value, value)。has_value=False 表示缓存未命中。"""
    entry = _role_cache.get(key)
    if entry is None:
        return False, False
    ts, val = entry
    if (_time.time() - ts) > _ROLE_CACHE_TTL:
        return False, False
    return True, val


def _role_cache_set(key: str, value: bool) -> None:
    _role_cache[key] = (_time.time(), value)


def invalidate_role_cache(user_id: str = "") -> None:
    """角色变更时调用，清除缓存。user_id 为空则清空全部。"""
    with _role_cache_lock:
        if user_id:
            user_id = str(user_id)
            # 清除该用户的所有角色缓存
            keys_to_del = [k for k in _role_cache if k.endswith(":" + user_id)]
            for k in keys_to_del:
                _role_cache.pop(k, None)
        else:
            _role_cache.clear()


def is_admin(user_id: str) -> bool:
    """检查用户是否为系统管理员（基于 r.id='admin'）。"""
    if not user_id:
        return False
    user_id = str(user_id)
    cache_key = "admin:" + user_id
    has, val = _role_cache_get(cache_key)
    if has:
        return val
    c = _conn()
    try:
        row = c.execute(
            "SELECT 1 FROM user_roles ur JOIN roles r ON ur.role_id = r.id "
            "WHERE ur.user_id = %s AND r.id = 'admin'",
            (user_id,),
        ).fetchone()
        result = row is not None
        _role_cache_set(cache_key, result)
        return result
    finally:
        c.close()


def is_project_admin(user_id: str, project: str = None) -> bool:
    """检查用户是否为项目管理员。

    判断逻辑（任一满足即返回 True）：
    1. 系统级 admin 角色
    2. 系统级 project_admin 角色
    3. 指定 project 时：项目内 owner 或 admin 角色
    """
    if not user_id:
        return False
    user_id = str(user_id)
    if is_admin(user_id):
        return True
    cache_key = "project_admin:" + user_id
    has, val = _role_cache_get(cache_key)
    if has:
        result = val
    else:
        c = _conn()
        try:
            row = c.execute(
                "SELECT 1 FROM user_roles ur JOIN roles r ON ur.role_id = r.id "
                "WHERE ur.user_id = %s AND r.id = 'project_admin'",
                (user_id,),
            ).fetchone()
            result = row is not None
            _role_cache_set(cache_key, result)
        finally:
            c.close()
    if result:
        return True
    if project:
        level = get_project_level(user_id, project)
        if level in ("owner", "admin"):
            return True
    return False


def is_project_member(user_id: str) -> bool:
    """检查用户是否拥有项目成员系统角色（r.id='project_member'）。"""
    if not user_id:
        return False
    user_id = str(user_id)
    cache_key = "project_member:" + user_id
    has, val = _role_cache_get(cache_key)
    if has:
        return val
    c = _conn()
    try:
        row = c.execute(
            "SELECT 1 FROM user_roles ur JOIN roles r ON ur.role_id = r.id "
            "WHERE ur.user_id = %s AND r.id = 'project_member'",
            (user_id,),
        ).fetchone()
        result = row is not None
        _role_cache_set(cache_key, result)
        return result
    finally:
        c.close()


def get_user_system_role(user_id: str) -> str:
    """
    获取用户的最高优先级系统角色。
    优先级：admin > project_admin > project_member > 自定义角色 > ""
    """
    if not user_id:
        return ""
    user_id = str(user_id)
    c = _conn()
    try:
        for role_id in ("admin", "project_admin", "project_member"):
            row = c.execute(
                "SELECT 1 FROM user_roles WHERE user_id=%s AND role_id=%s",
                (user_id, role_id),
            ).fetchone()
            if row:
                return role_id
        # 检查是否有自定义角色
        row = c.execute(
            "SELECT r.id FROM user_roles ur JOIN roles r ON ur.role_id=r.id "
            "WHERE ur.user_id=%s AND r.is_system=0 LIMIT 1",
            (user_id,),
        ).fetchone()
        return row["id"] if row else ""
    finally:
        c.close()


# ═══════════════════════════════════════════════════════════════
# 项目访问与级别（v3 — project_members 表）
# ═══════════════════════════════════════════════════════════════


def get_user_projects(user_id: str) -> list[str]:
    """获取用户可访问的项目列表。admin → 全部 active 项目，其他 → project_members 表。"""
    if not user_id:
        return []
    user_id = str(user_id)
    c = _conn()
    try:
        if is_admin(user_id):
            # ★ admin 也只返回 active 项目（与 docstring 及 _project.py:153 注释一致）。
            #   归档项目由 /api/projects 单独查询 status='archived' 返回（_project.py:160-161），
            #   不依赖本函数；中间件自动恢复当前项目时也不会误选归档项目。
            return [
                row["name"]
                for row in c.execute(
                    "SELECT name FROM projects WHERE status='active'"
                ).fetchall()
            ]
        return sorted(
            {
                r["project"]
                for r in c.execute(
                    "SELECT DISTINCT project FROM project_members WHERE user_id = %s",
                    (user_id,),
                ).fetchall()
            }
        )
    except Exception as e:
        _log.error("[get_user_projects] 异常: %s", e)
        return []
    finally:
        c.close()


def can_view(project: str, user_id: str) -> bool:
    """检查用户是否能访问指定项目。admin → True，其他 → project_members 表。"""
    if not user_id or not project:
        return False
    user_id = str(user_id)
    if is_admin(user_id):
        return True
    c = _conn()
    try:
        return (
            c.execute(
                "SELECT 1 FROM project_members WHERE user_id=%s AND project=%s",
                (user_id, project),
            ).fetchone()
            is not None
        )
    except Exception as e:
        _log.error("[can_view] 异常: %s", e)
        return False
    finally:
        c.close()


def get_project_level(user_id: str, project: str) -> str:
    """
    获取用户在项目中的身份（两层模型 v3）。
    - admin → "owner"
    - project_members 表 → "owner" 或 "member"
    - 无记录 → ""
    """
    if not user_id or not project:
        return ""
    user_id = str(user_id)
    if is_admin(user_id):
        return "owner"
    c = _conn()
    try:
        row = c.execute(
            "SELECT role FROM project_members WHERE user_id=%s AND project=%s",
            (user_id, project),
        ).fetchone()
        return row["role"] if row else ""
    except Exception as e:
        _log.error("[get_project_level] 异常: %s", e)
        return ""
    finally:
        c.close()


def get_project_owner(project: str) -> str:
    """获取项目的 owner 用户 ID（凭证继承用）。

    查找顺序：
    1. project_members 表中 role='owner' 的用户
    2. projects 表的 created_by 字段
    3. 返回 ""（无 owner）
    """
    if not project:
        return ""
    c = _conn()
    try:
        row = c.execute(
            "SELECT user_id FROM project_members WHERE project=%s AND role='owner' LIMIT 1",
            (project,),
        ).fetchone()
        if row and row["user_id"]:
            return row["user_id"]
        row = c.execute(
            "SELECT created_by FROM project_meta WHERE project=%s", (project,)
        ).fetchone()
        if row and row["created_by"]:
            return row["created_by"]
        return ""
    except Exception as e:
        _log.warning("[get_project_owner] 查询异常: %s", e)
        return ""
    finally:
        c.close()


# 项目版本 CRUD


def create_project_version(
    project: str,
    version_label: str,
    description: str = "",
    created_by: str = "",
    status: str = "draft",
) -> int | None:
    """创建项目版本。返回 version_id，失败返回 None。"""
    if not project or not version_label:
        return None

    last_error = None
    for attempt in range(3):
        c = _conn()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            # 先 ping 检测连接是否存活
            c.ping(reconnect=False)
            cur = c.execute(
                "INSERT INTO project_versions(project, version_label, description, status, created_by, created_at) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                (project, version_label.strip(), description, status, created_by, now),
            )
            c.commit()
            vid = cur.lastrowid
            return vid
        except Exception as e:
            last_error = e
            _log.info(f"[Version] 创建版本失败 (attempt {attempt+1}/3): {e}")
            try:
                c.rollback()
            except Exception as _e:
                _log.debug("[project_auth] 回滚异常: %s", _e, exc_info=True)
            if attempt < 2:
                import time

                time.sleep(1)
        finally:
            try:
                c.close()
            except Exception as _e:
                _log.debug("[project_auth] 关闭连接异常: %s", _e, exc_info=True)

    _log.error(f"[Version] 创建版本最终失败: {last_error}")
    return None


def activate_project_version(project: str, version_id: int) -> bool:
    """激活指定版本（同时将其他 active 版本设为 archived）。
    ★ D-1-2: 原 draft → 改为 archived（需求规格 §4.8）。
    ★ 修复: 激活失败时回滚归档操作，避免项目丢失活跃版本。
    """
    c = _conn()
    try:
        c.execute(
            "UPDATE project_versions SET status='archived' WHERE project=%s AND status='active'",
            (project,),
        )
        cur = c.execute(
            "UPDATE project_versions SET status='active', activated_at=%s WHERE id=%s AND project=%s",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), version_id, project),
        )
        ok = cur.rowcount > 0
        if ok:
            c.commit()
        else:
            c.rollback()
        return ok
    except Exception as e:
        _log.error("[Manager] activate_project_version 异常: %s", e)
        with guard("activate_project_version: c.rollback()", log=_log):
            c.rollback()
        return False
    finally:
        try:
            c.close()
        except (OSError, ConnectionError) as _e:
            _log.debug("[project_auth] 关闭连接异常: %s", _e, exc_info=True)


def _serialize_version_dt(d: dict) -> dict:
    """将版本 dict 中的 datetime 字段统一格式化为 'YYYY-MM-DD HH:MM:SS' 字符串。

    Flask jsonify 默认把 datetime 序列化成 HTTP-date（如 'Sun, 09 Aug 2026 10:18:22 GMT'），
    对中文界面不友好；此处根因收敛，避免前端逐处 replace。"""
    out = {}
    for k, v in d.items():
        if isinstance(v, datetime):
            out[k] = v.strftime("%Y-%m-%d %H:%M:%S")
        else:
            out[k] = v
    return out


def get_active_version(project: str) -> dict | None:
    """获取项目的活跃版本。"""
    if not project:
        return None
    c = None
    try:
        c = _conn()
        row = c.execute(
            "SELECT * FROM project_versions WHERE project=%s AND status='active'",
            (project,),
        ).fetchone()
        return _serialize_version_dt(dict(row)) if row else None

    finally:
        if c:
            c.close()


def get_active_version_label(project: str) -> str:
    """获取项目活跃版本的标签（用于文件命名）。"""
    v = get_active_version(project)
    return v["version_label"] if v else ""


# ═══════════════════════════════════════════════════
# ★ D-2: 软删除与恢复
# ═══════════════════════════════════════════════════


def delete_project_version(project: str, version_id: int) -> bool:
    """软删除版本：将状态设为 deleted + 记录 deleted_at。
    仅允许 draft/archived 状态的版本被删除，active 版本拒绝删除。
    """
    c = _conn()
    try:
        row = c.execute(
            "SELECT status FROM project_versions WHERE id=%s AND project=%s",
            (version_id, project),
        ).fetchone()
        if not row:
            return False
        if dict(row).get("status") == "active":
            _log.warning("[VersionDelete] 拒绝删除 active 版本: id=%s", version_id)
            return False
        c.execute(
            "UPDATE project_versions SET status='deleted', deleted_at=%s WHERE id=%s AND project=%s",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), version_id, project),
        )
        c.commit()
        return True
    except Exception as e:
        _log.error("[VersionDelete] 软删除异常: %s", e)
        return False
    finally:
        try:
            c.close()
        except (OSError, ConnectionError) as _e:
            _log.debug("[project_auth] 关闭连接异常: %s", _e, exc_info=True)


def restore_project_version(project: str, version_id: int) -> bool:
    """恢复已软删除的版本：将 deleted 版本恢复为 draft 状态 + 清除 deleted_at。"""
    c = _conn()
    try:
        row = c.execute(
            "SELECT status FROM project_versions WHERE id=%s AND project=%s",
            (version_id, project),
        ).fetchone()
        if not row:
            return False
        if dict(row).get("status") != "deleted":
            _log.warning(
                "[VersionRestore] 版本非 deleted 状态，无需恢复: id=%s status=%s",
                version_id,
                dict(row).get("status"),
            )
            return False
        c.execute(
            "UPDATE project_versions SET status='draft', deleted_at=NULL WHERE id=%s AND project=%s",
            (version_id, project),
        )
        c.commit()
        return True
    except Exception as e:
        _log.error("[VersionRestore] 恢复异常: %s", e)
        return False
    finally:
        try:
            c.close()
        except (OSError, ConnectionError) as _e:
            _log.debug("[project_auth] 关闭连接异常: %s", _e, exc_info=True)


def purge_project_version(project: str, version_id: int) -> bool:
    """物理删除版本：从 project_versions 表中永久删除记录。

    ★ 新增: 修复物理删除链断裂缺陷。
    仅允许 deleted 状态的版本被物理删除，防止误删活跃版本。

    物理删除范围：
    - project_versions 表中的版本记录
    - 关联表通过 ON DELETE CASCADE 自动清理（source_documents, system_feature_list, nav_node, nav_edge）
    - 逻辑关联表（test_cases, executions 等）通过 version_id 清理

    Args:
        project: 项目名（安全过滤，防止跨项目删除）
        version_id: 版本ID

    Returns:
        True if deleted, False if not found or not in deleted state
    """
    c = _conn()
    try:
        # 状态校验：只允许物理删除已软删除的版本
        row = c.execute(
            "SELECT status, version_label FROM project_versions WHERE id=%s AND project=%s",
            (version_id, project),
        ).fetchone()
        if not row:
            _log.warning("[VersionPurge] 版本不存在: id=%s project=%s", version_id, project)
            return False
        row_dict = dict(row)
        if row_dict.get("status") != "deleted":
            _log.warning(
                "[VersionPurge] 拒绝物理删除非 deleted 状态版本: id=%s status=%s",
                version_id,
                row_dict.get("status"),
            )
            return False

        version_label = row_dict.get("version_label", "")

        for table in _logical_tables:
            with guard("purge_project_version: c.execute( f'DELETE FROM {table} WHE", log=_log):
                c.execute(
                    f"DELETE FROM {table} WHERE version_id=%s",
                    (version_id,),
                )

        # 物理删除版本记录（FK 关联表通过 CASCADE 自动清理）
        c.execute(
            "DELETE FROM project_versions WHERE id=%s AND project=%s",
            (version_id, project),
        )
        c.commit()
        _log.info(
            "[VersionPurge] 物理删除版本: project=%s id=%s label=%s",
            project,
            version_id,
            version_label,
        )
        return True
    except Exception as e:
        _log.error("[VersionPurge] 物理删除异常: %s", e)
        with guard("purge_project_version: c.rollback()", log=_log):
            c.rollback()
        return False
    finally:
        try:
            c.close()
        except (OSError, ConnectionError) as _e:
            _log.debug("[project_auth] 关闭连接异常: %s", _e, exc_info=True)


def get_project_versions(project: str) -> list[dict]:
    """获取项目的所有版本列表（按时间倒序）。"""
    if not project:
        return []
    c = None
    try:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM project_versions WHERE project=%s ORDER BY created_at DESC",
            (project,),
        ).fetchall()
        return [_serialize_version_dt(dict(r)) for r in rows]

    finally:
        if c:
            c.close()


def update_project_version(version_id: int, project: str = "", **fields) -> bool:
    """更新版本信息（version_label, description, status）。
    ★ 修复: 添加 project 参数防止跨项目越权操作。
    """
    updates = {
        k: v
        for k, v in fields.items()
        if k in {"version_label", "description", "status"}
    }
    if not updates:
        return False
    c = _conn()
    try:
        from src.core.db_manager import validate_column_names

        validate_column_names(list(updates.keys()))
        sets = ", ".join(f"{k}=%s" for k in updates)
        # ★ v6.0: 添加 project 过滤，防止跨项目越权
        if project:
            ok = (
                c.execute(
                    f"UPDATE project_versions SET {sets} WHERE id=%s AND project=%s",
                    list(updates.values()) + [version_id, project],
                ).rowcount
                > 0
            )
        else:
            ok = (
                c.execute(
                    f"UPDATE project_versions SET {sets} WHERE id=%s",
                    list(updates.values()) + [version_id],
                ).rowcount
                > 0
            )
        c.commit()
        return ok
    except Exception as e:
        _log.error("[Manager] update_project_version 异常: %s", e)
        return False
    finally:

        c.close()


def archive_project_version(version_id: int, project: str = "") -> bool:
    """★ PV-008: 归档版本（仅允许 active/draft → archived，禁止 archived/deleted 重复归档）。
    ★ 修复: 添加 project 参数防止跨项目越权操作。
    """
    c = _conn()
    try:
        # 状态合法性校验（添加 project 过滤）
        if project:
            row = c.execute(
                "SELECT status FROM project_versions WHERE id=%s AND project=%s",
                (version_id, project),
            ).fetchone()
        else:
            row = c.execute(
                "SELECT status FROM project_versions WHERE id=%s", (version_id,)
            ).fetchone()
        if not row:
            return False
        current = dict(row).get("status", "")
        if current == "archived":
            _log.warning("[VersionArchive] 版本已归档: id=%s", version_id)
            return False
        if current == "deleted":
            _log.warning("[VersionArchive] 拒绝归档已删除版本: id=%s", version_id)
            return False
        if project:
            ok = (
                c.execute(
                    "UPDATE project_versions SET status='archived' WHERE id=%s AND project=%s",
                    (version_id, project),
                ).rowcount
                > 0
            )
        else:
            ok = (
                c.execute(
                    "UPDATE project_versions SET status='archived' WHERE id=%s",
                    (version_id,),
                ).rowcount
                > 0
            )
        c.commit()
        return ok
    except Exception as e:
        _log.error("[Manager] archive_project_version 异常: %s", e)
        return False
    finally:

        c.close()
