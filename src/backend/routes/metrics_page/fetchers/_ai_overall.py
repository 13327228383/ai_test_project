# -*- coding: utf-8 -*-
"""
AI 综合健康获取器（Flaky / 幻觉分类）
"""

import logging
from src.core.guarded import guard

from ._utils import (
    _db_conn,
    _current_project_id,
    _current_version_id,
    _num,
    latest_run_id_for_metrics,
)

_log = logging.getLogger(__name__)


def _fetch_ai_overall(conn=None, run_id=None) -> dict:
    """从 ai_health_metrics 表读取 AI 综合健康深度数据。

    ★ 2026-07-13: 不再使用全局共享 run_id，改为按本 fetcher 关心的 metric_type
    集合查找"包含这些指标的最近 run"，避免最新 run 缺 step3 指标时整板块全空。

    可选参数:
    - conn: 共享 DB 连接，传入则使用且不关闭；未传入则自开自关
    - run_id: 已废弃（保留签名兼容），传入将被忽略
    """
    result = {
        "cove": None,
        "flaky": None,
        "hallucination": None,
        "deep_check": None,
        "rule_check": None,
        "case_quality": None,
        # ★ v5.4(2026-08-12): 补齐 metrics-def.js 取值路径——
        #   evidence_coverage / case_completeness 原先指向 ai_overall.*
        #   但后端未返回，导致 metrics-def.js render() 取到 null。
        #   现从 run_summary 表和 case_store 补充这两个字段。
        "evidence_coverage": None,
        "case_completeness": None,
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
        # ★ 按本 fetcher 关心的 metric_type 查找最近 run（非全局最新 run）
        version_id = _current_version_id()
        run_id = latest_run_id_for_metrics(
            conn,
            project,
            version_id,
            [
                "cross_check",
                "deep_check",
                "rule_check",
                "case_modeling_quality",
                "flaky",
            ],
        )
        if not run_id:
            result["note"] = "暂无执行数据（请先执行流水线）"
            return result

        result["cove"] = None

        # --- Flaky ---
        flaky_row = conn.execute(
            "SELECT value, total FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=4 AND metric_type='flaky' AND metric_name='flaky_count' LIMIT 1",
            (project, run_id),
        ).fetchone()
        if flaky_row and _num(flaky_row["total"], 0) > 0:
            fc = _num(flaky_row["value"], 0)
            ft = _num(flaky_row["total"], 0)
            result["flaky"] = {
                "count": fc,
                "total": ft,
                "rate": round(fc / max(ft, 1) * 100, 2),
            }

        # --- 幻觉分类明细 ---
        hallu = {
            "api_mismatch": 0,
            "ui_element_missing": 0,
            "enum_invalid": 0,
            "total": 0,
        }
        for row in conn.execute(
            "SELECT metric_name, value FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=3 AND metric_type='cross_check'",
            (project, run_id),
        ).fetchall():
            mn = row["metric_name"]
            v = _num(row["value"], 0)
            if mn == "api_mismatch":
                hallu["api_mismatch"] = v
                hallu["total"] += v
            elif mn == "ui_element_missing":
                hallu["ui_element_missing"] = v
                hallu["total"] += v
            elif mn == "enum_value_invalid":
                hallu["enum_invalid"] = v
                hallu["total"] += v

        case_row = conn.execute(
            "SELECT total FROM ai_health_metrics WHERE project=%s AND run_id=%s AND step=3 AND total > 0 LIMIT 1",
            (project, run_id),
        ).fetchone()
        total_cases = _num(case_row["total"], 0) if case_row else 0
        hallu["total_cases"] = total_cases
        hallu["rate"] = round(hallu["total"] / max(total_cases, 1) * 100, 2)
        result["hallucination"] = hallu

        # --- deep_check / rule_check（沉睡指标，复用 hallucination.total_cases，同 step=3）---
        deep_row = conn.execute(
            "SELECT SUM(value) AS cnt FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=3 AND metric_type='deep_check' "
            "AND metric_name='ai_deep_issues'",
            (project, run_id),
        ).fetchone()
        deep_cnt = _num(deep_row["cnt"], 0) if deep_row else 0
        result["deep_check"] = {
            "count": deep_cnt,
            "rate": round(deep_cnt / max(total_cases, 1) * 100, 2),
        }

        rule_row = conn.execute(
            "SELECT SUM(value) AS cnt FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=3 AND metric_type='rule_check' "
            "AND metric_name='rule_warnings'",
            (project, run_id),
        ).fetchone()
        rule_cnt = _num(rule_row["cnt"], 0) if rule_row else 0
        result["rule_check"] = {
            "count": rule_cnt,
            "rate": round(rule_cnt / max(total_cases, 1) * 100, 2),
        }

        # --- case_modeling_quality（用例建模质量，沉睡指标）---
        q_avg_row = conn.execute(
            "SELECT AVG(value) AS avg_val FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=3 AND metric_type='case_modeling_quality' "
            "AND metric_name='avg_quality_score'",
            (project, run_id),
        ).fetchone()
        avg_score = None
        if q_avg_row and q_avg_row["avg_val"] is not None:
            avg_score = round(_num(q_avg_row["avg_val"], 0), 2)

        q_sum_rows = conn.execute(
            "SELECT metric_name, SUM(value) AS total_val FROM ai_health_metrics "
            "WHERE project=%s AND run_id=%s AND step=3 AND metric_type='case_modeling_quality' "
            "AND metric_name IN ('low_quality_count','generated_count') "
            "GROUP BY metric_name",
            (project, run_id),
        ).fetchall()
        low_quality_count = 0
        generated_count = 0
        for qr in q_sum_rows:
            if qr["metric_name"] == "low_quality_count":
                low_quality_count = _num(qr["total_val"], 0)
            elif qr["metric_name"] == "generated_count":
                generated_count = _num(qr["total_val"], 0)
        result["case_quality"] = {
            "avg_score": avg_score,
            "low_quality_count": low_quality_count,
            "generated_count": generated_count,
            "low_quality_rate": (
                round(low_quality_count / generated_count * 100, 2)
                if generated_count > 0
                else None
            ),
        }

        # ★ v5.4(2026-08-12): 补齐 evidence_coverage、pass_rate、evidence_calibrated_confidence
        #   metrics-def.js path='ai_overall.evidence_coverage' 原先是死路径，
        #   dashboard.html 绕开用 /api/run-summary-latest 单独取。
        #   现在在此处直接补上，让 metrics-def.js render() 也能正确取值。
        #
        # ★ v6.3(2026-08-13): 增加 fallback —— run_summary 为空时从 executions 表实时计算，
        #   避免 Dashboard 显示空值（演示友好）。
        with guard("_fetch_ai_overall: rs_row = conn.execute( 'SELECT evide", log=_log):
            rs_row = conn.execute(
                "SELECT evidence_coverage, pass_rate, evidence_calibrated_confidence "
                "FROM run_summary "
                "WHERE project=%s AND version_id=%s ORDER BY created_at DESC LIMIT 1",
                (project, version_id),
            ).fetchone()
            if rs_row and rs_row["evidence_coverage"] is not None:
                result["evidence_coverage"] = round(
                    _num(rs_row["evidence_coverage"], 0), 2
                )
            if rs_row and rs_row["pass_rate"] is not None:
                result["pass_rate"] = round(
                    _num(rs_row["pass_rate"], 0), 2
                )
            if rs_row and rs_row["evidence_calibrated_confidence"] is not None:
                result["evidence_calibrated_confidence"] = round(
                    _num(rs_row["evidence_calibrated_confidence"], 0), 2
                )

        # ★ Fallback：run_summary 为空时，从 executions 表实时计算
        if (result.get("evidence_coverage") is None
                or result.get("pass_rate") is None
                or result.get("evidence_calibrated_confidence") is None):
            with guard("_fetch_ai_overall: fb_row = conn.execute( 'SELECT COUNT", log=_log):
                fb_row = conn.execute(
                    "SELECT COUNT(*) as total, "
                    "SUM(CASE WHEN LOWER(status)='pass' THEN 1 ELSE 0 END) as passed, "
                    "AVG(confidence) as avg_conf, "
                    "SUM(CASE WHEN confidence > 0 THEN 1 ELSE 0 END) as with_conf "
                    "FROM executions WHERE project=%s AND version_id=%s",
                    (project, version_id),
                ).fetchone()
                if fb_row and _num(fb_row["total"], 0) > 0:
                    total = _num(fb_row["total"], 0)
                    passed = _num(fb_row["passed"], 0)
                    avg_conf = float(fb_row["avg_conf"] or 0)
                    with_conf = _num(fb_row["with_conf"], 0)

                    if result.get("evidence_coverage") is None:
                        result["evidence_coverage"] = round(with_conf / total * 100, 2)
                    if result.get("pass_rate") is None:
                        result["pass_rate"] = round(passed / total * 100, 2)
                    if result.get("evidence_calibrated_confidence") is None:
                        result["evidence_calibrated_confidence"] = round(avg_conf, 2)

        # ★ v5.4(2026-08-12): 补齐 case_completeness（从 case_store）
        #   metrics-def.js path='ai_overall.case_completeness' 原先是死路径，
        #   dashboard.html 绕开用 /api/intent-metrics 单独取。
        with guard("_fetch_ai_overall: from src.pipeline.utils.case_store i", log=_log):
            from src.pipeline.utils.case_store import case_store as _cs

            _cm = _cs.compute_case_metrics()
            if _cm.get("has_data"):
                result["case_completeness"] = round(
                    _num(_cm.get("case_completeness"), 0), 2
                )

    except Exception as e:
        _log.warning("[metrics] _fetch_ai_overall 异常: %s", e)
        result["note"] = f"读取异常: {e}"
    finally:
        if _owns_conn and conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("metric + ai + conn.close: %s", _e, exc_info=True)

    return result
