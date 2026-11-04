# -*- coding: utf-8 -*-
"""
p0_plugins.py — P0 治理插件指标 API
====================================

提供:
  GET  /api/p0-plugin-metrics  → 聚合返回 Harness/Scorecard/TokenBudget/FlakyGuard 指标

实现要点:
  * 不 new Plugin() 实例获取 _stats（每次请求都是新实例，永远为 0）
  * 从 ai_health_metrics 表中读取 metric_type 为 harness_governance/scorecard/token_budget/flaky/flaky_guard 的持久化数据
  * 若 DB 中尚未写入对应字段 → 如实返回 {"ok": True, "...": None}，前端显示 "未采集"
"""

from flask import Blueprint, jsonify
import logging

_log = logging.getLogger(__name__)

p0_plugins_bp = Blueprint("p0_plugins", __name__)


def _num(v, default=None):
    try:
        if v is None:
            return default
        return int(v)
    except (ValueError, TypeError):
        try:
            return float(v)
        except (ValueError, TypeError):
            return default


@p0_plugins_bp.route("/api/p0-plugin-metrics")
def api_p0_plugin_metrics():
    """返回 P0 治理插件指标。"""
    out = {
        "ok": True,
        "dangerous_intercepts": None,
        "avg_confidence": None,
        "evidence_calibrated_confidence": None,  # ★ v7: 证据引擎校准置信度
        "evidence_ai_raw_confidence": None,  # ★ L1: AI原始声称（对比校准前后差异）
        "evidence_coverage": None,  # ★ v7: 独立证据覆盖率
        "evidence_cold_start": None,  # ★ v7: 是否冷启动
        "evidence_calibration_samples": None,  # ★ M3: 校准样本总数
        "total_evaluations": None,
        "truncation_count": None,
        "flaky_count": None,
        "token_usage_ratio": None,
    }

    # --- 从 session 获取当前项目 ---
    try:
        from flask import session

        project = session.get("project")
    except Exception:
        project = None
    if not project:
        out["note"] = "请先在主页面选择项目"
        return jsonify(out)

    # --- 获取数据库连接（复用 health.py） ---
    conn = None
    try:
        from src.backend.routes.health import _get_conn

        conn = _get_conn()
    except Exception as e:
        out["note"] = f"数据库异常: {e}"
        return jsonify(out)

    try:
        run_row = conn.execute(
            "SELECT run_id FROM ai_health_metrics WHERE project=%s ORDER BY created_at DESC LIMIT 1",
            (project,),
        ).fetchone()
        if not run_row:
            out["note"] = "暂无执行数据（请先执行流水线）"
            return jsonify(out)
        run_id = run_row["run_id"]

        # --- Harness Governance: dangerous_intercepts + token_used/token_budget_total ---
        for row in conn.execute(
            "SELECT metric_name, value FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND metric_type='harness_governance'",
            (project, run_id),
        ).fetchall():
            mn = row["metric_name"]
            if mn == "dangerous_intercepts":
                out["dangerous_intercepts"] = _num(row["value"], 0)
            elif mn == "token_used_total":
                out["token_used"] = _num(row["value"], 0)
            elif mn == "token_budget_total":
                out["token_budget_total"] = _num(row["value"], 0)

        if out.get("token_used") and out.get("token_budget_total"):
            out["token_usage_ratio"] = round(
                out["token_used"] / max(1, out["token_budget_total"]), 4
            )

        # --- Scorecard Oracle: avg_score + total_evaluations ---
        for row in conn.execute(
            "SELECT metric_name, value FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND metric_type='scorecard'",
            (project, run_id),
        ).fetchall():
            mn = row["metric_name"]
            if mn == "avg_score":
                out["avg_confidence"] = _num(row["value"], 0)
            elif mn == "total_evaluations":
                out["total_evaluations"] = _num(row["value"], 0)

        # --- Token Budget: truncations ---
        for row in conn.execute(
            "SELECT metric_name, value FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND metric_type='token_budget'",
            (project, run_id),
        ).fetchall():
            if row["metric_name"] == "truncations":
                out["truncation_count"] = _num(row["value"], 0)

        # --- Flaky: flaky_count（与领域 1 中同字段，此处保留以支持旧前端） ---
        flaky_row = conn.execute(
            "SELECT value FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=4 AND metric_type='flaky' AND metric_name='flaky_count' LIMIT 1",
            (project, run_id),
        ).fetchone()
        if flaky_row and flaky_row["value"]:
            out["flaky_count"] = _num(flaky_row["value"], 0)

        # --- ★ v7: 证据引擎指标（从 run_summary.summary_json 读取） ---
        try:
            ev_row = conn.execute(
                "SELECT summary_json FROM run_summary "
                "WHERE project=%s AND run_id=%s ORDER BY created_at DESC LIMIT 1",
                (project, run_id),
            ).fetchone()
            if ev_row and ev_row["summary_json"]:
                import json as _json

                summary = (
                    _json.loads(ev_row["summary_json"])
                    if isinstance(ev_row["summary_json"], str)
                    else ev_row["summary_json"]
                )
                evidence = (
                    summary.get("evidence") if isinstance(summary, dict) else None
                )
                if isinstance(evidence, dict):
                    out["evidence_calibrated_confidence"] = evidence.get(
                        "calibrated_confidence"
                    )
                    out["evidence_ai_raw_confidence"] = evidence.get(
                        "ai_raw_confidence"
                    )  # ★ L1
                    out["evidence_coverage"] = evidence.get("coverage")
                    out["evidence_cold_start"] = evidence.get("cold_start_count", 0) > 0
                    out["evidence_calibration_samples"] = evidence.get(
                        "calibration_samples", 0
                    )  # ★ M3
                    out["evidence_verdict_distribution"] = evidence.get(
                        "verdict_distribution", {}
                    )  # ★ L3
        except Exception as _e:
            _log.debug(
                "[p0_plugins] 旧版本 run_summary 无 evidence 字段,跳过: %s",
                _e,
                exc_info=True,
            )

        # --- 字段缺失时的友好提示 ---
        has_any = any(v is not None and k != "ok" for k, v in out.items())
        if not has_any:
            out["note"] = (
                "P0 治理指标尚未写入 ai_health_metrics 表。请在 Step 4 pipeline 中开启 "
                "HarnessGovernance/ScorecardOracle/TokenBudget/FlakyGuard 的 on_after_intent 持久化输出。"
            )

    except Exception as e:
        _log.error("[api_p0_plugin_metrics] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _pass_e:
                _log.warning("非关键异常: %s", _pass_e, exc_info=True)
    return jsonify(out)
