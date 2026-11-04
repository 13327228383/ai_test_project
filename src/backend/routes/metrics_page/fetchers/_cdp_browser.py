# -*- coding: utf-8 -*-
"""
CDP 客户端浏览器指标获取器（2026-07-15）
================================================
读取 ai_health_metrics 表中 cdp_browser 类型的指标，展示客户端浏览器模式的：
- 连接次数/成功率/连接耗时
- 断开次数
- 用例执行数/通过率/执行耗时
- 错误统计
"""

import json
import logging

from ._utils import (
    _db_conn,
    _current_project_id,
    _current_version_id,
    _num,
    latest_run_id_for_metrics,
)

_log = logging.getLogger(__name__)

_CDP_TYPE = "cdp_browser"


def _fetch_cdp_browser(conn=None) -> dict:
    """读取最近一次 run 的 CDP 浏览器指标。可选传入共享 conn。"""
    result = {
        "attach_count": 0,
        "attach_success": 0,
        "attach_success_rate": None,
        "attach_failed": 0,
        "detach_count": 0,
        "connect_duration_ms_avg": None,
        "connect_duration_ms_max": None,
        "case_executed": 0,
        "case_passed": 0,
        "case_failed": 0,
        "pass_rate": None,
        "exec_duration_ms_avg": None,
        "exec_duration_ms_max": None,
        "action_count_total": 0,
        "screenshot_size_total": 0,
        "error_count": 0,
        "errors": [],
        "note": None,
    }

    project = _current_project_id()
    if not project:
        result["note"] = "需先选择项目"
        return result

    _owns_conn = conn is None
    if _owns_conn:
        conn = _db_conn()
    if conn is None:
        result["note"] = "无法连接数据库"
        return result

    try:
        version_id = _current_version_id()
        run_id = latest_run_id_for_metrics(conn, project, version_id, [_CDP_TYPE])
        if not run_id:
            result["note"] = "暂无 CDP 浏览器指标数据"
            return result

        rows = (
            conn.execute(
                "SELECT metric_name, category, value, total, details "
                "FROM ai_health_metrics "
                "WHERE project=%s AND run_id=%s AND metric_type=%s "
                "ORDER BY created_at ASC",
                (project, run_id, _CDP_TYPE),
            ).fetchall()
            or []
        )

        if not rows:
            result["note"] = "暂无 CDP 浏览器指标数据"
            return result

        # 聚合
        _connect_durations = []
        _exec_durations = []
        _errors_detail = []

        for row in rows:
            _name = row["metric_name"]
            _val = _num(row["value"], 0)
            _details_raw = row["details"]

            if _name == "attach_count":
                result["attach_count"] += 1
                if _val > 0:
                    result["attach_success"] += 1
                else:
                    result["attach_failed"] += 1
                # 记录连接耗时
                if _details_raw:
                    try:
                        _d = (
                            json.loads(_details_raw)
                            if isinstance(_details_raw, str)
                            else _details_raw
                        )
                        _dur = _num(_d.get("duration_ms"), None)
                        if _dur is not None:
                            _connect_durations.append(_dur)
                    except Exception as _e:
                        _log.debug(
                            "[cdp_browser] 解析 attach 耗时异常: %s", _e, exc_info=True
                        )
            elif _name == "detach_count":
                result["detach_count"] += 1
            elif _name == "case_executed":
                result["case_executed"] += 1
                if _val > 0:
                    result["case_passed"] += 1
                else:
                    result["case_failed"] += 1
            elif _name == "exec_duration_ms":
                if _val is not None:
                    _exec_durations.append(_val)
            elif _name == "action_count":
                result["action_count_total"] += int(_val or 0)
            elif _name == "screenshot_size":
                result["screenshot_size_total"] += int(_val or 0)
            elif _name == "error_count":
                result["error_count"] += 1
                if _details_raw and len(_errors_detail) < 10:
                    try:
                        _d = (
                            json.loads(_details_raw)
                            if isinstance(_details_raw, str)
                            else _details_raw
                        )
                        _errors_detail.append(
                            {
                                "case_id": _d.get("case_id", ""),
                                "error_type": _d.get("error_type", ""),
                                "error_message": str(_d.get("error_message", ""))[:200],
                            }
                        )
                    except Exception as _e:
                        _log.debug(
                            "[cdp_browser] 解析错误详情异常: %s", _e, exc_info=True
                        )

        # 计算汇总
        if _connect_durations:
            result["connect_duration_ms_avg"] = round(
                sum(_connect_durations) / len(_connect_durations), 1
            )
            result["connect_duration_ms_max"] = round(max(_connect_durations), 1)

        if _exec_durations:
            result["exec_duration_ms_avg"] = round(
                sum(_exec_durations) / len(_exec_durations), 1
            )
            result["exec_duration_ms_max"] = round(max(_exec_durations), 1)

        if result["case_executed"] > 0:
            result["pass_rate"] = round(
                100.0 * result["case_passed"] / result["case_executed"], 1
            )

        if result["attach_count"] > 0:
            result["attach_success_rate"] = round(
                100.0 * result["attach_success"] / result["attach_count"], 1
            )

        result["errors"] = _errors_detail

    except Exception as e:
        _log.warning("[metrics] _fetch_cdp_browser 异常: %s", e)
        result["note"] = f"读取异常: {e}"
    finally:
        if _owns_conn and conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("conn.close 失败: %s", _e, exc_info=True)
    return result
