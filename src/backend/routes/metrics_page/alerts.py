# -*- coding: utf-8 -*-
"""
通用指标告警计算模块（2026-07-15）
================================
从 metric_thresholds 表读取阈值配置，对比 fetcher 返回的指标数据，
生成告警等级（normal/warning/error/critical）。

设计原则（AGENTS.md §4.2 通用性）：
- 通用：适用于任何 metric_type，不硬编码特定指标
- 可配置：阈值存 DB，前端可编辑
- 不重复造轮子：复用现有 fetcher 返回的数据
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)

# 告警等级优先级（数字越大越严重）
_LEVEL_PRIORITY = {"normal": 0, "warning": 1, "error": 2, "critical": 3}
_LEVEL_TEXT = {"normal": "正常", "warning": "关注", "error": "异常", "critical": "严重"}


def _load_thresholds(conn, metric_type: str) -> list[dict]:
    """从 DB 加载指定 metric_type 的阈值配置。"""
    if not conn or not metric_type:
        return []
    try:
        rows = (
            conn.execute(
                "SELECT metric_name, field, warn_threshold, error_threshold, "
                "critical_threshold, compare_op, description "
                "FROM metric_thresholds WHERE metric_type=%s AND enabled=1",
                (metric_type,),
            ).fetchall()
            or []
        )
        return [dict(r) for r in rows]
    except Exception as e:
        _log.warning("[alerts] 加载阈值异常: %s", e)
        return []


def _compare(value: float, threshold: float, op: str) -> bool:
    """根据比较运算符判断 value 是否触发阈值。"""
    try:
        if op == ">=":
            return value >= threshold
        if op == "<=":
            return value <= threshold
        if op == "==":
            return value == threshold
        if op == ">":
            return value > threshold
        if op == "<":
            return value < threshold
    except (TypeError, ValueError):
        return False
    return False


def _compute_field_alert(value: Any, thresholds: dict) -> dict:
    """计算单个字段的告警等级。

    thresholds 格式: {warn, error, critical, compare_op, description}
    返回: {level, message}
    """
    if value is None:
        return {"level": "neutral", "message": "无数据"}

    try:
        v = float(value)
    except (TypeError, ValueError):
        return {"level": "neutral", "message": "非数值"}

    op = thresholds.get("compare_op", "<")
    desc = thresholds.get("description", "")

    # 依次检查 critical / error / warning（按严重程度从高到低）
    for level in ("critical", "error", "warning"):
        key = f"{level}_threshold"
        thr = thresholds.get(key)
        if thr is None:
            continue
        # 比较逻辑：对于 "<" 运算符，value 小于阈值则触发
        # 对于 ">" 运算符，value 大于阈值则触发
        if _compare(v, float(thr), op):
            return {
                "level": level,
                "message": (
                    f"{desc}（当前: {v}，阈值: {thr} {op}）"
                    if desc
                    else f"当前值 {v} 触发 {level} 阈值 {thr}"
                ),
            }

    return {"level": "normal", "message": desc or "正常"}


def compute_alerts(conn, metric_type: str, metric_data: dict) -> dict:
    """计算指定域的告警信息。

    Args:
        conn: DB 连接
        metric_type: 指标类型（如 "cdp_browser"）
        metric_data: fetcher 返回的指标数据 dict

    Returns:
        {
            "fields": {field_name: {level, message}},
            "max_level": "normal" | "warning" | "error" | "critical",
            "has_alert": bool,
        }
    """
    result = {
        "fields": {},
        "max_level": "normal",
        "has_alert": False,
    }

    if not metric_data or not isinstance(metric_data, dict):
        return result

    # 无数据（note 字段存在且无实际指标）
    if (
        metric_data.get("note")
        and not metric_data.get("attach_count")
        and not metric_data.get("case_executed")
    ):
        result["fields"]["_note"] = {"level": "neutral", "message": metric_data["note"]}
        return result

    thresholds = _load_thresholds(conn, metric_type)
    if not thresholds:
        return result

    max_priority = 0
    for th in thresholds:
        field = th.get("field", "")
        if not field or field not in metric_data:
            continue
        value = metric_data.get(field)
        alert = _compute_field_alert(value, th)
        result["fields"][field] = alert

        pri = _LEVEL_PRIORITY.get(alert["level"], 0)
        if pri > max_priority:
            max_priority = pri

    result["max_level"] = next(
        (lv for lv, pri in _LEVEL_PRIORITY.items() if pri == max_priority),
        "normal",
    )
    result["has_alert"] = max_priority >= 1  # warning 及以上
    return result
