# -*- coding: utf-8 -*-
"""
P0 治理获取器（Harness / Scorecard / TokenBudget / FlakyGuard）
"""

import logging

from ._utils import (
    _db_conn,
    _current_project_id,
    _current_version_id,
    _num,
    latest_run_id_for_metrics,
)

_log = logging.getLogger(__name__)


def _fetch_p0_governance(conn=None, run_id=None) -> dict:
    """从 ai_health_metrics 读取 P0 治理指标。

    ★ 2026-07-13: 不再使用全局共享 run_id，改为按本 fetcher 关心的 metric_type
    集合查找最近 run。run_id 参数已废弃（保留签名兼容）。
    """
    result = {
        "harness": None,
        "scorecard": None,
        "token_budget": None,
        "flaky_guard": None,
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
        # ★ 按本 fetcher 关心的 metric_type 查找最近 run
        version_id = _current_version_id()
        run_id = latest_run_id_for_metrics(
            conn,
            project,
            version_id,
            ["harness_governance", "scorecard", "token_budget", "flaky_guard"],
        )
        if not run_id:
            result["note"] = "暂无执行数据"
            return result

        # --- Harness Governance ---
        # DB 实际: harness_governance.rejected, harness_governance.total_attempts
        # 不是 dangerous_intercepts/tool_limit_violations/token_used/token_budget_total
        hg_rows = conn.execute(
            "SELECT metric_name, value, total FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND metric_type='harness_governance'",
            (project, run_id),
        ).fetchall()
        if hg_rows:
            hg = {}
            for r in hg_rows:
                mn = r["metric_name"]
                if mn in ("dangerous_intercepts", "rejected"):
                    hg["dangerous_intercepts_total"] = _num(r["value"], 0)
                elif mn in ("tool_limit_violations", "total_attempts"):
                    hg["total_attempts_total"] = _num(r["value"], 0)
            # 拦截率 = rejected / total_attempts * 100
            if hg.get("total_attempts_total", 0) > 0:
                hg["intercept_rate"] = round(
                    hg.get("dangerous_intercepts_total", 0)
                    / max(1, hg.get("total_attempts_total", 1))
                    * 100, 4,
                )
            result["harness"] = hg

        # --- Scorecard Oracle ---
        sco_rows = conn.execute(
            "SELECT metric_name, value FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND metric_type='scorecard'",
            (project, run_id),
        ).fetchall()
        if sco_rows:
            sco = {}
            for r in sco_rows:
                mn = r["metric_name"]
                if mn == "avg_score":
                    sco["avg_score"] = _num(r["value"], 0)
                elif mn == "total_evaluations":
                    sco["total_evaluations"] = _num(r["value"], 0)
                elif "dim_" in mn:
                    sco[mn] = _num(r["value"], 0)
            result["scorecard"] = sco

        # --- Token Budget ---
        # DB 实际: token_budget.total_used, token_budget.budget_ok
        tb_rows = conn.execute(
            "SELECT metric_name, value FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND metric_type='token_budget'",
            (project, run_id),
        ).fetchall()
        if tb_rows:
            tb = {}
            for r in tb_rows:
                mn = r["metric_name"]
                if mn in ("truncations", "total_used"):
                    tb["total_used"] = _num(r["value"], 0)
                elif mn in ("budget_exceeded", "budget_ok"):
                    tb["budget_ok"] = _num(r["value"], 0)
            result["token_budget"] = tb

        # --- Flaky Guard ---
        fg_rows = conn.execute(
            "SELECT metric_name, value FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND metric_type='flaky_guard'",
            (project, run_id),
        ).fetchall()
        if fg_rows:
            fg = {}
            for r in fg_rows:
                mn = r["metric_name"]
                if mn.startswith("env_"):
                    fg["environment_issues"] = _num(r["value"], 0)
                elif mn.startswith("timing_"):
                    fg["timing_issues"] = _num(r["value"], 0)
                elif mn.startswith("data_"):
                    fg["data_issues"] = _num(r["value"], 0)
                elif mn.startswith("ai_hallucination_"):
                    fg["ai_hallucination_issues"] = _num(r["value"], 0)
            result["flaky_guard"] = fg

        has_any = any(v is not None for k, v in result.items() if k != "note")
        if not has_any:
            result["note"] = (
                "P0 指标尚未写入 ai_health_metrics。请确认 Step 4 执行 pipeline 中已开启 "
                "HarnessGovernance/ScorecardOracle/TokenBudget/FlakyGuard 的持久化输出。"
            )

    except Exception as e:
        _log.warning("[metrics] _fetch_p0_governance 异常: %s", e)
        result["note"] = f"读取异常: {e}"
    finally:
        if _owns_conn and conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("metric + conn.close + close(): %s", _e, exc_info=True)

    return result
