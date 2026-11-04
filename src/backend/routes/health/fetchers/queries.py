# -*- coding: utf-8 -*-
from __future__ import annotations

"""
health.fetchers._queries — 健康度数据查询层
=============================================
封装所有 ai_health_metrics 表的数据库查询逻辑。
"""

import logging

_log = logging.getLogger(__name__)

# ★ SUP-016: 连接池缓存，避免每次请求创建新连接
_conn_pool = None


def _get_conn():
    """获取加密数据库连接（带连接池复用）。"""
    global _conn_pool
    try:
        if _conn_pool is None or not _conn_pool.open:
            from src.models.base import get_conn

            _conn_pool = get_conn()
        return _conn_pool
    except Exception:
        from src.models.base import get_conn

        _conn_pool = get_conn()
        return _conn_pool


def _latest_run_id(project: str, version_id: str = None) -> str:
    """获取项目最近一次运行的 run_id。兼容旧表无 run_id/created_at 列。

    ★ 修复：优先返回非空 run_id，跳过 token_usage 产生的空 run_id 记录。
    ★ SUP-016: 复用连接池连接，不再按次创建/关闭。
    ★ 2026-07-13: 添加 version_id 参数，支持按版本隔离查询。
    """
    try:
        conn = _get_conn()
        # 先尝试验证 run_id 列是否存在
        try:
            if version_id:
                row = conn.execute(
                    "SELECT run_id FROM ai_health_metrics "
                    "WHERE project=%s AND version_id=%s AND run_id IS NOT NULL AND run_id != '' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (project, version_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT run_id FROM ai_health_metrics "
                    "WHERE project=%s AND run_id IS NOT NULL AND run_id != '' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (project,),
                ).fetchone()
            return row["run_id"] if row else ""
        except Exception:
            # 旧表缺少 run_id/created_at 列，回退到 recorded_at
            try:
                row = conn.execute(
                    "SELECT id FROM ai_health_metrics WHERE project=%s ORDER BY recorded_at DESC LIMIT 1",
                    (project,),
                ).fetchone()
                return str(row["id"]) if row else ""
            except Exception:
                return ""
    except Exception as _e:
        _log.debug("health + metric + ai: %s", _e, exc_info=True)
        return ""


def _query_metrics(conn, project, run_id, step, metric_types, extra_cols=None):
    """
    通用指标行查询。

    参数
    ----
    conn : 数据库连接
    project : str — 项目名
    run_id : str — 运行 ID
    step : int — 步骤号
    metric_types : list[str] — metric_type 过滤列表
    extra_cols : list[str] | None — 额外 SELECT 列（如 ['total', 'category']）

    返回
    ----
    list[Row] — 查询结果行列表
    """
    cols = "metric_name, value"
    if extra_cols:
        cols += ", " + ", ".join(extra_cols)
    placeholders = ", ".join(["%s"] * len(metric_types))
    sql = (
        f"SELECT {cols} FROM ai_health_metrics "
        f"WHERE project=%s AND run_id=%s AND step=%s AND metric_type IN ({placeholders})"
    )
    params = (project, run_id, step) + tuple(metric_types)
    return conn.execute(sql, params).fetchall()


def _integrate_metrics(rows, key_col="metric_name", val_col="value", key_map=None):
    """
    将查询结果行列表整合为 {名称: 值} 字典。

    参数
    ----
    rows : list[Row]
    key_col : str — 作为 key 的列名
    val_col : str — 作为 value 的列名
    key_map : dict[str, str] | None — 可选 key 名称映射 {原名: 映射名}

    返回
    ----
    dict
    """
    result = {}
    for row in rows:
        name = row[key_col]
        if key_map and name in key_map:
            name = key_map[name]
        result[name] = row[val_col]
    return result
