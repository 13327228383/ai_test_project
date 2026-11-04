# -*- coding: utf-8 -*-
"""配置历史相关函数（config_history 表）"""
import json
import logging
from datetime import datetime

from src.users._user_db_core import _conn

_log = logging.getLogger(__name__)

# _normalize_scope 被 action_logs 和 config_history 共用，从同级模块导入
from src.users.audit_logs._action_logs import _normalize_scope, log_action


def record_config_change(
    user_id: str,
    key: str,
    old_val: str,
    new_val: str,
    *,
    scope_type: str = "project",
    scope_id: str = "",
    conn=None,
):
    """记录单条配置变更；必须显式传 scope_type/scope_id。

    性能优化：支持外部传入 conn 复用连接，避免多次 ping。
    """
    if not user_id or old_val == new_val:
        return
    scope_type, scope_id = _normalize_scope(scope_type, scope_id)
    own_conn = conn is None
    c = conn
    try:
        if c is None:
            c = _conn()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        old_json = (
            json.dumps(str(old_val), ensure_ascii=False)
            if old_val is not None
            else None
        )
        new_json = (
            json.dumps(str(new_val), ensure_ascii=False)
            if new_val is not None
            else None
        )
        c.execute(
            "INSERT INTO config_history(scope_type, scope_id, section, old_value, new_value, user_id, created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (scope_type, scope_id, key, old_json, new_json, user_id, now),
        )
        # 只有自己打开了连接才 commit+close，外部传入的连接由调用方管理
        if own_conn:
            c.commit()

    finally:
        if own_conn and c:
            c.close()


def get_config_history(
    *, scope_type: str = "project", scope_id: str = "", limit: int = 50
):
    """查询配置变更历史；必须显式传 scope_type/scope_id。"""
    c = None
    try:
        c = _conn()
        scope_type, scope_id = _normalize_scope(scope_type, scope_id)
        rows = c.execute(
            "SELECT id, section, old_value, new_value, user_id, created_at "
            "FROM config_history WHERE scope_type=%s AND scope_id=%s ORDER BY id DESC LIMIT %s",
            (scope_type, scope_id, limit),
        ).fetchall()
        result = []
        for r in rows:
            old_val = r["old_value"]
            new_val = r["new_value"]
            if isinstance(old_val, (dict, list)):
                old_val = json.dumps(old_val, ensure_ascii=False)
            if isinstance(new_val, (dict, list)):
                new_val = json.dumps(new_val, ensure_ascii=False)
            result.append(
                {
                    "id": r["id"],
                    "section": r["section"],
                    "old_value": str(old_val) if old_val is not None else "",
                    "new_value": str(new_val) if new_val is not None else "",
                    "user_id": r["user_id"],
                    "created_at": r["created_at"],
                }
            )
        return result

    finally:
        if c:
            c.close()


def rollback_config(project: str, key: str, target_value: str, user_id: str):
    """MySQL 化：从 project_configs 表回滚单个配置项。返回 (success, old_value)。"""
    from src.core.db_manager import get_connection

    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT config_json FROM project_configs WHERE project=%s", (project,)
        ).fetchone()
        if not row or not row.get("config_json"):
            return False, ""
        config = (
            json.loads(row["config_json"])
            if isinstance(row["config_json"], str)
            else dict(row["config_json"])
        )
        if not isinstance(config, dict):
            return False, ""
        old_val = config.get(key, "")
        # 智能类型转换
        try:
            target_value = json.loads(target_value)
        except (json.JSONDecodeError, TypeError) as _e:
            _log.debug("[config_history] JSON 转换失败(§5): %s", _e)
        config[key] = target_value
        conn.execute(
            "INSERT INTO project_configs (project, config_json) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE config_json=%s",
            (
                project,
                json.dumps(config, ensure_ascii=False),
                json.dumps(config, ensure_ascii=False),
            ),
        )
        conn.commit()
        record_config_change(
            user_id,
            key,
            json.dumps(old_val, ensure_ascii=False) if old_val else "",
            json.dumps(target_value, ensure_ascii=False),
            scope_type="project",
            scope_id=project,
        )
        log_action(
            user_id,
            "config-rollback",
            scope_type="project",
            scope_id=project,
            detail=f"{key}: {old_val} → {target_value}",
        )
        return True, str(old_val) if old_val is not None else ""
    except Exception as e:
        _log.warning("[rollback_config] 失败: %s", str(e)[:120])
        return False, ""
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)
