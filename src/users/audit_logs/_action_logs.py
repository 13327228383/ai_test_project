# -*- coding: utf-8 -*-
"""操作日志相关函数（action_logs 表）"""
import json
import logging
import threading
from datetime import datetime

from src.users._user_db_core import (
    _conn,
    ACTION_LOG_RETENTION_DAYS,
    ACTION_LOG_MAX_ROWS,
)


_log = logging.getLogger(__name__)


def _normalize_scope(scope_type: str, scope_id: str = ""):
    scope_type = str(scope_type or "system").strip()
    scope_id = str(scope_id or "").strip()
    if scope_type not in {"system", "project", "user", "global"}:
        raise ValueError("无效 scope_type: %s" % scope_type)
    if scope_type in {"system", "global"}:
        scope_id = ""
    if scope_type in {"project", "user"} and not scope_id:
        raise ValueError("%s scope 必须提供 scope_id" % scope_type)
    return scope_type, scope_id


# ★ 降频计数器：_cleanup_action_logs 每 100 次 log_action 调用才执行一次
_cleanup_counter = 0
_cleanup_lock = threading.Lock()
_CLEANUP_EVERY_N = 100


def _cleanup_action_logs(c) -> None:
    """操作日志轻量清理：保留最近 90 天，且全表最多 100000 行。
    降频策略：每 100 次调用才执行一次清理，减少 MySQL 写入压力。
    """
    global _cleanup_counter
    with _cleanup_lock:
        _cleanup_counter += 1
        should_cleanup = _cleanup_counter % _CLEANUP_EVERY_N == 0
    if not should_cleanup:
        return
    try:
        c.execute(
            "DELETE FROM action_logs WHERE created_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
            (ACTION_LOG_RETENTION_DAYS,),
        )
    except Exception as _e:
        _log.debug("cleanup + cleanup + cleanup: %s", _e, exc_info=True)
    try:
        c.execute(
            "DELETE FROM action_logs WHERE id NOT IN ("
            "SELECT id FROM (SELECT id FROM action_logs ORDER BY id DESC LIMIT %s) t"
            ")",
            (ACTION_LOG_MAX_ROWS,),
        )
    except Exception as _e:
        _log.debug("execute: %s", _e, exc_info=True)


def count_action_logs(
    *,
    scope_type: str = "",
    scope_id: str = "",
    user_id: str = "",
    action: str = "",
):
    """★ 操作日志总条数（与 list_action_logs 相同过滤口径，等价于无 LIMIT 列表）。

    供审计页展示真实匹配总数：前端不再用「当前页 items.length」冒充总数，
    否则在有过滤/分页时统计数字会失真（根因：原 count=len(items)）。
    """
    c = None
    try:
        c = _conn()
        where = []
        params = []
        if scope_type:
            where.append("scope_type=%s")
            params.append(scope_type)
        if scope_id:
            where.append("scope_id=%s")
            params.append(scope_id)
        if user_id:
            where.append("user_id=%s")
            params.append(user_id)
        if action:
            where.append("action LIKE %s")
            params.append("%%%s%%" % action)
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        row = c.execute(
            "SELECT COUNT(*) AS total FROM action_logs" + where_clause,
            params,
        ).fetchone()
        return row["total"] if isinstance(row, dict) else (row[0] if row else 0)
    finally:
        if c:
            c.close()


def list_action_logs(
    *,
    scope_type: str = "",
    scope_id: str = "",
    user_id: str = "",
    action: str = "",
    limit: int = 100,
    offset: int = 0
):
    """查询操作日志列表，支持按 scope/user/action 过滤。返回 dict 列表。"""
    c = None
    try:
        c = _conn()
        where = []
        params = []
        if scope_type:
            where.append("scope_type=%s")
            params.append(scope_type)
        if scope_id:
            where.append("scope_id=%s")
            params.append(scope_id)
        if user_id:
            where.append("user_id=%s")
            params.append(user_id)
        if action:
            where.append("action LIKE %s")
            params.append("%%%s%%" % action)
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        params.extend([limit, offset])
        rows = c.execute(
            "SELECT id, user_id, scope_type, scope_id, action, detail, ip_address, created_at "
            "FROM action_logs" + where_clause + " ORDER BY id DESC LIMIT %s OFFSET %s",
            params,
        ).fetchall()
        result = []
        for r in rows:
            d = (
                dict(r)
                if isinstance(r, dict)
                else {
                    "id": r[0],
                    "user_id": r[1],
                    "scope_type": r[2],
                    "scope_id": r[3],
                    "action": r[4],
                    "detail": r[5],
                    "ip_address": r[6],
                    "created_at": r[7],
                }
            )
            # detail 是 JSON 字段，统一转为字符串
            if d.get("detail") is not None and not isinstance(d["detail"], str):
                d["detail"] = json.dumps(d["detail"], ensure_ascii=False)
            result.append(d)
        return result
    finally:
        if c:
            c.close()


def list_login_tracking(
    *, user_id: str = "", action: str = "", limit: int = 100, offset: int = 0
):
    """查询登录追踪日志列表，支持按 user_id/action 过滤。返回 dict 列表。"""
    c = None
    try:
        c = _conn()
        where = []
        params = []
        if user_id:
            where.append("user_id=%s")
            params.append(user_id)
        if action:
            where.append("action=%s")
            params.append(action)
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        params.extend([limit, offset])
        rows = c.execute(
            "SELECT id, user_id, action, ip_address, user_agent, detail, created_at "
            "FROM login_tracking"
            + where_clause
            + " ORDER BY id DESC LIMIT %s OFFSET %s",
            params,
        ).fetchall()
        result = []
        for r in rows:
            d = (
                dict(r)
                if isinstance(r, dict)
                else {
                    "id": r[0],
                    "user_id": r[1],
                    "action": r[2],
                    "ip_address": r[3],
                    "user_agent": r[4],
                    "detail": r[5],
                    "created_at": r[6],
                }
            )
            result.append(d)
        return result
    finally:
        if c:
            c.close()


def count_login_tracking(*, user_id: str = "", action: str = ""):
    """★ 登录追踪总条数（与 list_login_tracking 相同过滤口径）。"""
    c = None
    try:
        c = _conn()
        where = []
        params = []
        if user_id:
            where.append("user_id=%s")
            params.append(user_id)
        if action:
            where.append("action=%s")
            params.append(action)
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        row = c.execute(
            "SELECT COUNT(*) AS total FROM login_tracking" + where_clause,
            params,
        ).fetchone()
        return row["total"] if isinstance(row, dict) else (row[0] if row else 0)
    finally:
        if c:
            c.close()


def log_action(
    user_id: str,
    action: str,
    *,
    scope_type: str = "system",
    scope_id: str = "",
    detail=None,
    ip_address: str = ""
):
    """记录操作日志；必须显式传 scope_type/scope_id，不再兼容旧 project 参数。"""
    if not user_id:
        return
    scope_type, scope_id = _normalize_scope(scope_type, scope_id)
    # 将 detail 转换为合法 JSON：字符串会被包装，字典直接序列化
    if detail is None or detail == "":
        detail_json = None
    elif isinstance(detail, (dict, list)):
        detail_json = json.dumps(detail, ensure_ascii=False)
    else:
        detail_json = json.dumps({"message": str(detail)}, ensure_ascii=False)
    c = None
    try:
        c = _conn()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # ★ PV-007: 记录 IP 地址
        c.execute(
            "INSERT INTO action_logs(user_id,action,scope_type,scope_id,detail,ip_address,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (user_id, action, scope_type, scope_id, detail_json, ip_address or "", now),
        )
        _cleanup_action_logs(c)
        c.commit()

    # 配置变更历史
    finally:
        if c:
            c.close()
