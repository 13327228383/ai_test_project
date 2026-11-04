# -*- coding: utf-8 -*-
"""
健康度趋势 + 最新运行明细 路由

【2026-06-18 拆分】从 health.py 物理搬迁，零业务逻辑变更。
"""

from datetime import datetime, timedelta, timezone

from flask import jsonify
import logging

_log = logging.getLogger(__name__)

from src.backend.state import _current_project
from src.models.permission import check_admin_or_permission
from .health import health_bp, ACCURACY_LABELS, ACCURACY_LABEL_TEXT
from .fetchers.queries import _get_conn, _latest_run_id
from .fetchers.format import _confidence


# ═══════════════════════════════════════════════════════════════
# GET /api/health/trends?days=30 — 趋势数据
# ═══════════════════════════════════════════════════════════════


@health_bp.route("/api/health/trends")
def api_health_trends():
    try:
        ok, err = check_admin_or_permission("health.view", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )

        project = _current_project()
        if not project:
            return (
                jsonify({"ok": False, "error": "请先选择项目", "has_data": False}),
                400,
            )

        try:
            days = int(__import__("flask").request.args.get("days", "30"))
        except (ValueError, TypeError):
            days = 30

        conn = _get_conn()
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )

            # 按 run_id + 日期聚合
            rows = conn.execute(
                """SELECT run_id, step, metric_type, metric_name, value, total, created_at
                   FROM ai_health_metrics
                   WHERE project=%s AND created_at >= %s
                   ORDER BY created_at ASC""",
                (project, cutoff),
            ).fetchall()
        finally:
            if conn:
                conn.close()
        if not rows:
            return jsonify({"ok": True, "has_data": False, "dates": [], "series": {}})

        # 按 run_id 分组聚合
        run_data = (
            {}
        )  # run_id → {step3_rate, step4_guard_rate, step4_verify_rate, date}
        for row in rows:
            rid = row["run_id"]
            if rid not in run_data:
                run_data[rid] = {
                    "date": str(row["created_at"])[:10],
                    "step3_errors": 0,
                    "step3_total": 0,
                    "step4_rejections": 0,
                    "step4_attempts": 0,
                    "step4_disagreements": 0,
                    "step4_verifications": 0,
                    "dominance_score_sum": 0.0,
                    "dominance_score_count": 0,
                }
            rd = run_data[rid]
            if (
                row["step"] == 3
                and row["metric_type"] == "cross_check"
                and row["metric_name"] == "hallucination_errors"
            ):
                rd["step3_errors"] += int(row["value"])
                rd["step3_total"] += int(row["total"])
            elif row["step"] == 4 and row["metric_type"] == "guard_reject":
                rd["step4_rejections"] += int(row["value"])
            elif row["step"] == 4 and row["metric_type"] == "action_attempt":
                rd["step4_attempts"] += int(row["value"])
            elif row["step"] == 4 and row["metric_type"] == "verification":
                if row["metric_name"] == "disagreement":
                    rd["step4_disagreements"] += int(row["value"])
                elif row["metric_name"] == "ui_pass":
                    rd["step4_verifications"] += int(
                        row["value"]
                    )  # 每条 ui_pass value 表示实际校验次数
            elif (
                row["step"] == 4
                and row["metric_type"] == "intent_dominance"
                and row["metric_name"] == "dominance_score"
            ):
                rd["dominance_score_sum"] += float(row["value"] or 0)
                rd["dominance_score_count"] += 1

        dates = []
        step3_rates = []
        step4_guard_rates = []
        step4_verify_rates = []
        intent_dominance_scores = []

        for rid in sorted(run_data, key=lambda r: run_data[r]["date"]):
            rd = run_data[rid]
            dates.append(rd["date"])
            s3 = round(rd["step3_errors"] / max(rd["step3_total"], 1) * 100, 2)
            s4g = round(
                rd["step4_rejections"]
                / max(rd["step4_rejections"] + rd["step4_attempts"], 1)
                * 100,
                2,
            )
            s4v = (
                round(
                    rd["step4_disagreements"] / max(rd["step4_verifications"], 1) * 100,
                    2,
                )
                if rd["step4_verifications"] > 0
                else 0
            )
            ids = (
                round(
                    rd["dominance_score_sum"] / max(rd["dominance_score_count"], 1), 1
                )
                if rd["dominance_score_count"] > 0
                else 0
            )
            step3_rates.append(s3)
            step4_guard_rates.append(s4g)
            step4_verify_rates.append(s4v)
            intent_dominance_scores.append(ids)

        return jsonify(
            {
                "ok": True,
                "has_data": True,
                "dates": dates,
                "series": {
                    "step3_hallucination_rate": {
                        "label": "Step 3 幻觉率",
                        "data": step3_rates,
                        "unit": "%",
                    },
                    "step4_guard_rate": {
                        "label": "Step 4 守卫拦截率",
                        "data": step4_guard_rates,
                        "unit": "%",
                    },
                    "step4_verify_rate": {
                        "label": "Step 4 校验不一致率",
                        "data": step4_verify_rates,
                        "unit": "%",
                    },
                    "intent_dominance_score": {
                        "label": "意图主导度",
                        "data": intent_dominance_scores,
                        "unit": "%",
                    },
                },
            }
        )
    except Exception as e:
        _log.error("[api_health_trends] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500


# ═══════════════════════════════════════════════════════════════
# GET /api/health/latest-run — 最新运行详细分解
# ═══════════════════════════════════════════════════════════════


@health_bp.route("/api/health/latest-run")
def api_health_latest_run():
    try:
        ok, err = check_admin_or_permission("health.view", project=_current_project())
        if not ok:
            return jsonify({"ok": False, "error": err}), (
                401 if err.startswith("请先登录") else 403
            )

        project = _current_project()
        if not project:
            return (
                jsonify({"ok": False, "error": "请先选择项目", "has_data": False}),
                400,
            )

        run_id = _latest_run_id(project)
        if not run_id:
            return jsonify({"ok": True, "has_data": False})

        conn = _get_conn()
        try:
            rows = conn.execute(
                """SELECT step, metric_type, metric_name, category, value, total, details, created_at
                   FROM ai_health_metrics
                   WHERE project=%s AND run_id=%s
                   ORDER BY step, metric_type, metric_name""",
                (project, run_id),
            ).fetchall()
        finally:
            if conn:
                conn.close()
        items = []
        for row in rows:
            item = {
                "step": row["step"],
                "metric_type": row["metric_type"],
                "metric_name": row["metric_name"],
                "category": row["category"],
                "value": row["value"],
                "total": row["total"],
                "details": row["details"],
                "created_at": row["created_at"],
                "confidence": {
                    "level": _confidence(
                        row["metric_type"], row["metric_name"], ACCURACY_LABELS
                    ),
                    "text": ACCURACY_LABEL_TEXT.get(
                        _confidence(
                            row["metric_type"], row["metric_name"], ACCURACY_LABELS
                        ),
                        "?",
                    ),
                },
            }
            items.append(item)

        return jsonify({"ok": True, "has_data": True, "run_id": run_id, "items": items})
    except Exception as e:
        _log.error("[api_health_latest_run] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500
