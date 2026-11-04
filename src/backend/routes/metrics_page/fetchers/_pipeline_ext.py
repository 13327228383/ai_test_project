# -*- coding: utf-8 -*-
"""
流水线扩展获取器（Step1/2/3/5 插件注册数）

★ 根因修复(2026-09-20)：插件注册数是进程级状态，且 step1/3/5 多在真实运行进程内懒注册，
常驻读端进程看不到 → 恒 0。现在优先读「运行期持久化快照」(ai_health_metrics.pipeline_ext)，
拿不到快照再回退读当前进程 bootstrap（兼容旧运行）。
"""

import logging
from src.core.guarded import guard

from ._utils import (
    _db_conn,
    _current_project_id,
    _current_version_id,
    latest_run_id_for_metrics,
)

_log = logging.getLogger(__name__)

_PLUGIN_STEPS = ("step1_datasource", "step2_analyzer", "step3_generator", "step5_report")


def _fetch_pipeline_ext() -> dict:
    result = {k: None for k in _PLUGIN_STEPS}
    result["note"] = None

    project = _current_project_id()
    if not project:
        result["note"] = "需先选择项目"
        return result

    # ── 1) 优先读最近一次 run 的持久化快照 ──
    with guard("_fetch_pipeline_ext: conn = _db_conn()", log=_log, level=logging.WARNING):
        conn = _db_conn()
        if conn:
            try:
                version_id = _current_version_id()
                run_id = latest_run_id_for_metrics(
                    conn, project, version_id, ["pipeline_ext"]
                )
                if run_id:
                    rows = conn.execute(
                        "SELECT metric_name, SUM(value) as v FROM ai_health_metrics "
                        "WHERE project=%s AND run_id=%s AND metric_type='pipeline_ext' "
                        "GROUP BY metric_name",
                        (project, run_id),
                    ).fetchall()
                    counts = {}
                    for r in rows:
                        base = r["metric_name"]
                        if base.endswith("_count"):
                            counts[base[:-6]] = int(r["v"] or 0)
                    if counts:
                        for k in _PLUGIN_STEPS:
                            if k in counts:
                                result[k] = {"plugins_registered_count": counts[k]}
                        return result
            finally:
                with guard("_fetch_pipeline_ext: conn.close()", log=_log):
                    conn.close()

    # ── 2) 回退：读当前进程 bootstrap（启动注册态） ──
    try:
        from src.pipeline.plugins.bootstrap import list_plugins

        plugins = list_plugins()
        if not plugins:
            result["note"] = "插件系统未初始化"
            return result

        if "step1_datasource" in plugins:
            ds = plugins["step1_datasource"]
            result["step1_datasource"] = {"plugins_registered_count": len(ds.list_datasources())}
        if "step2_analyzer" in plugins:
            ana = plugins["step2_analyzer"]
            result["step2_analyzer"] = {"plugins_registered_count": len(ana.list_strategies())}
        if "step3_generator" in plugins:
            gen = plugins["step3_generator"]
            result["step3_generator"] = {
                "plugins_registered_count": len(gen.list_generators())
                + len(gen.list_validators())
                + len(gen.list_templates())
            }
        if "step5_report" in plugins:
            rep = plugins["step5_report"]
            result["step5_report"] = {
                "plugins_registered_count": len(rep.list_sections())
                + len(rep.list_exporters())
                + len(rep.list_distributors())
            }

        if all(v is None for k, v in result.items() if k != "note"):
            result["note"] = "无已注册插件"
    except Exception as e:
        _log.warning("[metrics] _fetch_pipeline_ext 异常: %s", e)
        result["note"] = f"读取异常: {e}"

    return result
