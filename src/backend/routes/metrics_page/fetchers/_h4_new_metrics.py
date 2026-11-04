# -*- coding: utf-8 -*-
"""
H4 新增指标获取器（2026-07-10）
================================
读取 ai_health_metrics 表中 6 个新增 metric_type 的最新 run_id 数据：
- slice_coverage    (Step2 切片覆盖率)
- control_type      (Step3 控件类型命中分布)
- parallel_exec     (Step4 并行执行统计)
- manual_retest     (Step5 人工补测建议)
- hash_reuse        (Step2 RAG章节哈希复用率)
- incremental_regen (Step4 增量重生成功率)
"""

import json
import logging

from ._utils import _db_conn, _current_project_id, _current_version_id, _num

_log = logging.getLogger(__name__)

# 6 个新增 metric_type
_H4_TYPES = (
    "slice_coverage",
    "control_type",
    "parallel_exec",
    "manual_retest",
    "hash_reuse",
    "incremental_regen",
)


def _fetch_h4_new_metrics(conn=None) -> dict:
    """读取最近一次 run 的 6 个新增指标。可选传入共享 conn。"""
    result = {
        "slice_coverage": None,
        "control_type": None,
        "parallel_exec": None,
        "manual_retest": None,
        "hash_reuse": None,
        "incremental_regen": None,
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
        # 取最新 run_id（6 个新类型中任意一个的最新 run）
        placeholders = ",".join(["%s"] * len(_H4_TYPES))
        version_id = _current_version_id()
        if version_id:
            run_row = conn.execute(
                "SELECT run_id FROM ai_health_metrics "
                "WHERE project=%%s AND version_id=%%s AND metric_type IN (%s) "
                "ORDER BY created_at DESC LIMIT 1" % placeholders,
                (project, version_id, *_H4_TYPES),
            ).fetchone()
        else:
            run_row = conn.execute(
                "SELECT run_id FROM ai_health_metrics "
                "WHERE project=%%s AND metric_type IN (%s) "
                "ORDER BY created_at DESC LIMIT 1" % placeholders,
                (project, *_H4_TYPES),
            ).fetchone()
        if not run_row:
            result["note"] = "暂无 H4 新增指标数据"
            return result
        run_id = run_row["run_id"]

        # 读取该 run 的所有新类型指标
        rows = (
            conn.execute(
                "SELECT metric_type, metric_name, value, total, details "
                "FROM ai_health_metrics "
                "WHERE project=%%s AND run_id=%%s AND metric_type IN (%s)"
                % placeholders,
                (project, run_id, *_H4_TYPES),
            ).fetchall()
            or []
        )

        # 按 metric_type 分组
        _grouped = {}
        for row in rows:
            mt = row.get("metric_type", "")
            if mt not in _grouped:
                _grouped[mt] = []
            _grouped[mt].append(row)

        # 解析 details JSON
        def _parse_details(row):
            d = row.get("details", "") or "{}"
            try:
                return json.loads(d) if isinstance(d, str) else (d or {})
            except (json.JSONDecodeError, TypeError):
                return {}

        # slice_coverage
        if "slice_coverage" in _grouped:
            _d = {}
            for r in _grouped["slice_coverage"]:
                _d[r["metric_name"]] = {
                    "value": _num(r.get("value"), 0),
                    "total": _num(r.get("total"), 0),
                }
                if r["metric_name"] == "coverage_rate":
                    _d["coverage_rate"].update(_parse_details(r))
            result["slice_coverage"] = _d

        # control_type
        if "control_type" in _grouped:
            _d = {}
            for r in _grouped["control_type"]:
                _d[r["metric_name"]] = {
                    "value": _num(r.get("value"), 0),
                    "total": _num(r.get("total"), 0),
                }
                if r["metric_name"] == "hit_rate":
                    _d["hit_rate"].update(_parse_details(r))
            result["control_type"] = _d

        # parallel_exec
        if "parallel_exec" in _grouped:
            _d = {}
            for r in _grouped["parallel_exec"]:
                _d[r["metric_name"]] = {
                    "value": _num(r.get("value"), 0),
                    "total": _num(r.get("total"), 0),
                }
                if r["metric_name"] == "throughput":
                    _d["throughput"].update(_parse_details(r))
            result["parallel_exec"] = _d

        # manual_retest
        if "manual_retest" in _grouped:
            _d = {}
            for r in _grouped["manual_retest"]:
                _d[r["metric_name"]] = {
                    "value": _num(r.get("value"), 0),
                    "total": _num(r.get("total"), 0),
                }
                if r["metric_name"] == "total":
                    _d["total"].update(_parse_details(r))
            result["manual_retest"] = _d

        # hash_reuse
        if "hash_reuse" in _grouped:
            _d = {}
            for r in _grouped["hash_reuse"]:
                _d[r["metric_name"]] = {
                    "value": _num(r.get("value"), 0),
                    "total": _num(r.get("total"), 0),
                }
                if r["metric_name"] == "reuse_rate":
                    _d["reuse_rate"].update(_parse_details(r))
            result["hash_reuse"] = _d

        # incremental_regen
        if "incremental_regen" in _grouped:
            _d = {}
            for r in _grouped["incremental_regen"]:
                _d[r["metric_name"]] = {
                    "value": _num(r.get("value"), 0),
                    "total": _num(r.get("total"), 0),
                }
                if r["metric_name"] == "success_rate":
                    _d["success_rate"].update(_parse_details(r))
            result["incremental_regen"] = _d

    except Exception as e:
        _log.warning("[metrics] _fetch_h4_new_metrics 异常: %s", e)
        result["note"] = f"读取异常: {e}"
    finally:
        if _owns_conn and conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("conn.close 失败: %s", _e, exc_info=True)

    return result
