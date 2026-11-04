# -*- coding: utf-8 -*-
"""
GET /api/health/summary — 最新运行聚合摘要

【2026-06-18 拆分】从 health.py 物理搬迁，零业务逻辑变更。
"""

from flask import jsonify
from src.core.guarded import guard
import logging

_log = logging.getLogger(__name__)

from src.backend.state import _current_project
from .health import health_bp, ACCURACY_LABELS, ACCURACY_LABEL_TEXT
from .fetchers.queries import _get_conn, _latest_run_id
from .fetchers.format import _build_labels, _build_health_score


def _get_embedding_status() -> dict:
    """获取 RAG 语义引擎状态（自动检测，无需配置环境变量）。"""
    try:
        from src.extensions.semantic_index import get_model_status

        status = get_model_status()
        if status and status.get("cache_path") is None:
            status["cache_path"] = ""
        return status
    except Exception:
        return {
            "mode": "unknown",
            "model": None,
            "dim": 0,
            "cache_path": "",
            "is_semantic": False,
        }


# ═══════════════════════════════════════════════════════════════
# GET /api/health/summary — 最新运行聚合摘要
# ═══════════════════════════════════════════════════════════════


@health_bp.route("/api/health/summary")
def api_health_summary():
    try:
        from src.core.config_loader import get_current_user
        from src.backend.helpers import _user_can_access_project
        from src.backend.state import _current_version

        uid = get_current_user()
        if not uid:
            return jsonify({"ok": False, "error": "请先登录"}), 401
        proj = _current_project()
        if proj and not _user_can_access_project(uid, proj):
            return jsonify({"ok": False, "error": "无权访问当前项目"}), 403

        project = _current_project()
        if not project:
            return (
                jsonify({"ok": False, "error": "请先选择项目", "has_data": False}),
                400,
            )

        version_id = _current_version()
        run_id = _latest_run_id(project, version_id)
        if not run_id:
            return jsonify(
                {
                    "ok": True,
                    "has_data": False,
                    "message": "暂无数据，请先执行流水线",
                    "embedding": _get_embedding_status(),
                }
            )

        # ★ SUP-016: 复用同一连接执行所有查询，减少连接创建开销
        import time as _time

        _t0 = _time.time()
        conn = _get_conn()
        _t_conn = _time.time() - _t0
        try:
            # ── Step 2 覆盖指标 ──
            s2 = {
                "coverage_forward": None,
                "orphan_modules": None,
                "uncovered_sections": None,
            }
            for row in conn.execute(
                "SELECT metric_name, value FROM ai_health_metrics WHERE project=%s AND run_id=%s AND step=2 AND metric_type='coverage'",
                (project, run_id),
            ).fetchall():
                s2[row["metric_name"]] = row["value"]

            # ★ Fallback：step2 coverage 为空时，从 executions + requirement_store 推算
            if s2["coverage_forward"] is None:
                with guard("api_health_summary: from src.pipeline.utils.requirement_", log=_log):
                    from src.pipeline.utils.requirement_store import requirement_store as _rs
                    from src.core.versioning import current_version_id as _cvid

                    reqs = _rs.list_requirements()
                    req_count = len(reqs) if reqs else 0
                    _vid = int(_cvid(project) or 0)
                    exec_row = conn.execute(
                        "SELECT COUNT(*) as total FROM executions WHERE project=%s AND version_id=%s",
                        (project, _vid),
                    ).fetchone()
                    case_count = int(exec_row["total"] or 0) if exec_row else 0
                    if req_count > 0 and case_count > 0:
                        s2["coverage_forward"] = round(
                            min(case_count, req_count) / req_count * 100, 1
                        )

            # ── Step 3 交叉校验 ──
            _s3_has_rows = False
            s3 = {
                "cross_check_api": 0,
                "cross_check_ui": 0,
                "cross_check_enum": 0,
                "deep_check": 0,
                "rule_check": 0,
                "total_cases": 0,
                "rate": 0,
            }
            # Step3 交叉校验可能按需求拆成多个 batch 分别 record（同 run_id 多行），
            # 这里按 metric_name 累加聚合，避免 last-write-wins 只体现最后一个 batch。
            _s3_map = {
                "api_mismatch": "cross_check_api",
                "ui_element_missing": "cross_check_ui",
                "enum_value_invalid": "cross_check_enum",
                "ai_deep_issues": "deep_check",
                "rule_warnings": "rule_check",
            }
            for row in conn.execute(
                "SELECT metric_name, value, total FROM ai_health_metrics WHERE project=%s AND run_id=%s AND step=3 AND metric_type IN ('cross_check','deep_check','rule_check')",
                (project, run_id),
            ).fetchall():
                _s3_has_rows = True
                mn = row["metric_name"]
                if mn in _s3_map:
                    s3[_s3_map[mn]] += int(row["value"] or 0)
                elif mn == "hallucination_errors":
                    # 每个 batch 的 total=该 batch 用例数，累加即为本次 run 参与校验的用例总数
                    s3["total_cases"] += int(row["total"] or 0)
            # 如果没有 total_cases，只从 cross_check 类型的行取，避免误取 quality_score 的 total=100
            if s3["total_cases"] == 0:
                r = conn.execute(
                    "SELECT total FROM ai_health_metrics WHERE project=%s AND run_id=%s AND step=3 AND metric_type='cross_check' AND total > 0 LIMIT 1",
                    (project, run_id),
                ).fetchone()
                if r:
                    s3["total_cases"] = int(r["total"])
            total_errors = (
                s3["cross_check_api"] + s3["cross_check_ui"] + s3["cross_check_enum"]
            )
            s3["rate"] = (
                round(total_errors / max(s3["total_cases"], 1) * 100, 2)
                if s3["total_cases"] > 0
                else None
            )
            # ★ 根因修复(2026-09-21)：本次 run 无任何 step3 校验行 → 未采集态，
            #   前端据此显示 — 而非伪装的"0 幻觉"（对齐三态设计）
            s3["collected"] = _s3_has_rows

            # ── Step 3 测试用例生成质量趋势 (KEEP-04 联动) ──
            s3_quality = {}
            q_rows = conn.execute(
                "SELECT metric_name, SUM(value) as total_val, MAX(total) as max_total "
                "FROM ai_health_metrics WHERE project=%s AND run_id=%s AND step=3 "
                "AND metric_type='case_modeling_quality' GROUP BY metric_name",
                (project, run_id),
            ).fetchall()
            for qr in q_rows:
                s3_quality[qr["metric_name"]] = float(qr["total_val"] or 0)
            # 计算平均质量分（多条记录取平均）
            q_avg_rows = conn.execute(
                "SELECT AVG(value) as avg_val FROM ai_health_metrics "
                "WHERE project=%s AND run_id=%s AND step=3 AND metric_type='case_modeling_quality' "
                "AND metric_name='avg_quality_score'",
                (project, run_id),
            ).fetchone()
            if q_avg_rows and q_avg_rows["avg_val"] is not None:
                s3_quality["avg_quality_score"] = round(float(q_avg_rows["avg_val"]), 1)

            # ── Step 4 守卫拦截 ──
            # ★ 根因修复(2026-09-21)：区分「未采集」与「真 0」——本次 run 无任何
            #   guard_reject/action_attempt 行时返回 None（前端显示 —），
            #   不再用默认 0 伪装成"采集过且零拦截"（对齐 2026-09-20 三态设计）。
            s4_action = {
                "guard_rejections": 0,
                "action_attempts": 0,
                "rate": 0,
                "breakdown": {},
            }
            _s4a_rows = conn.execute(
                "SELECT metric_type, metric_name, category, value FROM ai_health_metrics WHERE project=%s AND run_id=%s AND step=4 AND metric_type IN ('guard_reject','action_attempt')",
                (project, run_id),
            ).fetchall()
            for row in _s4a_rows:
                if row["metric_type"] == "guard_reject":
                    s4_action["guard_rejections"] += int(row["value"])
                    cat = row["category"] or "other"
                    s4_action["breakdown"][cat] = s4_action["breakdown"].get(
                        cat, 0
                    ) + int(row["value"])
                elif row["metric_type"] == "action_attempt":
                    s4_action["action_attempts"] += int(row["value"])
            if not _s4a_rows:
                # 本次 run 未采集任何守卫/动作指标 → 未采集态（None），前端显示 —
                s4_action["guard_rejections"] = None
                s4_action["action_attempts"] = None
                s4_action["rate"] = None
            else:
                # ★ 统一口径（2026-09-24）：守卫拦截率分母对齐项目约定 guard_reject/
                #   action_attempt（同 metrics_page/_step4_plugins.py）——原实现分子守卫
                #   额外计入分母(action+guard)，导致多名牌面板口径割裂。
                s4_action["rate"] = (
                    round(
                        s4_action["guard_rejections"] / max(s4_action["action_attempts"], 1)
                        * 100,
                        2,
                    )
                    if s4_action["action_attempts"] > 0
                    else 0.0
                )

            # ── Step 4 3轨校验 ──
            s4_verify = {
                "verification_count": 0,
                "disagreements": 0,
                "rate": 0,
                "ui_pass": 0,
                "api_pass": 0,
                "db_pass": 0,
            }
            for row in conn.execute(
                "SELECT metric_name, value FROM ai_health_metrics WHERE project=%s AND run_id=%s AND step=4 AND metric_type='verification'",
                (project, run_id),
            ).fetchall():
                mn = row["metric_name"]
                if mn == "ui_pass":
                    s4_verify["ui_pass"] += int(row["value"])
                    s4_verify["verification_count"] += int(row["value"])
                elif mn == "api_pass":
                    s4_verify["api_pass"] += int(row["value"])
                elif mn == "db_pass":
                    s4_verify["db_pass"] += int(row["value"])
                elif mn == "disagreement":
                    s4_verify["disagreements"] += int(row["value"])
            vc = max(s4_verify["verification_count"], 1)
            # 以各轨 pass 率计算
            s4_verify["rate"] = (
                round(s4_verify["disagreements"] / max(vc, 1) * 100, 2)
                if s4_verify["verification_count"] > 0
                else 0.0
            )

            # ── run_summary：用例级执行结果（★ 根因修复 2026-09-21）──
            # ai-health「校验达成率」tooltip 承诺的数据源是 run_summary.pass_rate，
            # 但后端从未产出该段。此前校验达成率只能依赖 ai_health_metrics 的
            # verification 三轨行，而统一执行落库层(excel_cases._update)不写健康指标，
            # 导致"已执行 9 例全 PASS"时该 KPI 仍显示 0%。
            # 现直接按 run_id 查 executions 每 case 最新状态，与 charts 页 pass_rate
            # 同源同构（PASS+FLAKY)/(终态总数)，非终态(NOT_EXECUTED/PRE_GATE)不入分母。
            run_summary = {
                "total": 0,
                "executed": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "pass_rate": 0.0,
            }
            with guard("api_health_summary: _exec_rows = conn.execute( ''' SELEC", log=_log, level=logging.WARNING):
                _exec_rows = conn.execute(
                    """
                    SELECT e.status AS status, COUNT(*) AS cnt
                    FROM executions e
                    JOIN (
                        SELECT MAX(id) AS mid FROM executions
                        WHERE project=%s AND run_id=%s GROUP BY case_id
                    ) t ON e.id = t.mid
                    GROUP BY e.status
                    """,
                    (project, run_id),
                ).fetchall()
                _st = {r["status"]: int(r["cnt"] or 0) for r in _exec_rows}
                _passed = _st.get("PASS", 0) + _st.get("FLAKY", 0)
                _failed = _st.get("FAIL", 0)
                _skipped = _st.get("SKIPPED", 0) + _st.get("SKIP", 0)
                _terminal = (
                    _passed + _failed + _skipped + _st.get("BLOCKED", 0)
                )
                run_summary = {
                    "total": sum(_st.values()),
                    "executed": _terminal,
                    "passed": _passed,
                    "failed": _failed,
                    "skipped": _skipped,
                    "pass_rate": round(_passed / _terminal * 100, 2)
                    if _terminal > 0
                    else 0.0,
                }

            # ── Flaky 检测率 ──
            flaky_data = {}
            flaky_row = conn.execute(
                "SELECT value, total FROM ai_health_metrics WHERE project=%s AND run_id=%s AND step=4 AND metric_type='flaky' AND metric_name='flaky_count' LIMIT 1",
                (project, run_id),
            ).fetchone()
            # ★ 安全修复：防御 None 和字符串类型，避免 TypeError
            if flaky_row:
                flaky_total = int(flaky_row["total"] or 0)
                if flaky_total > 0:
                    flaky_data["count"] = int(flaky_row["value"] or 0)
                    flaky_data["total"] = flaky_total
                    flaky_data["rate"] = round(
                        int(flaky_row["value"] or 0) / flaky_total * 100, 2
                    )

            # ── Step 4 操作缓存命中率 ──
            cache_data = {}
            cache_rows = conn.execute(
                "SELECT metric_name, SUM(value) as total FROM ai_health_metrics "
                "WHERE project=%s AND run_id=%s AND step=4 AND metric_type='op_cache' "
                "GROUP BY metric_name",
                (project, run_id),
            ).fetchall()
            cache_hits = sum(
                r["total"] for r in cache_rows if r["metric_name"] == "cache_hit"
            )
            cache_misses = sum(
                r["total"] for r in cache_rows if r["metric_name"] == "cache_miss"
            )
            cache_total = cache_hits + cache_misses
            if cache_total > 0:
                cache_data = {
                    "hits": cache_hits,
                    "misses": cache_misses,
                    "total": cache_total,
                    "rate": round(cache_hits / cache_total * 100, 2),
                }

            # ── Step 4 用例主导度 (v3.0) ──
            case_dominance = {
                "score": None,
                "case_total": 0,
                "actions_present": 0,
                "checks_present": 0,
                "verdict_recorded": 0,
                "recipe_only_pass": 0,
                "asset_rate": 0,
                "evidence_rate": 0,
                "verdict_rate": 0,
                "recipe_only_rate": 0,
            }
            dom_rows = conn.execute(
                "SELECT metric_name, SUM(value) as total_val, AVG(value) as avg_val, COUNT(*) as cnt "
                "FROM ai_health_metrics WHERE project=%s AND run_id=%s AND step=4 "
                "AND metric_type='case_dominance' GROUP BY metric_name",
                (project, run_id),
            ).fetchall()
            for dr in dom_rows:
                mn = dr["metric_name"]
                if mn == "dominance_score":
                    case_dominance["score"] = round(float(dr["avg_val"] or 0), 1)
                elif mn in case_dominance:
                    case_dominance[mn] = int(dr["total_val"] or 0)
            dom_total = max(int(case_dominance.get("case_total") or 0), 1)
            if case_dominance.get("case_total"):
                case_dominance["asset_rate"] = round(
                    case_dominance["actions_present"] / dom_total * 100, 1
                )
                case_dominance["evidence_rate"] = round(
                    case_dominance["checks_present"] / dom_total * 100, 1
                )
                case_dominance["verdict_rate"] = round(
                    case_dominance["verdict_recorded"] / dom_total * 100, 1
                )
                case_dominance["recipe_only_rate"] = round(
                    case_dominance["recipe_only_pass"] / dom_total * 100, 1
                )

            # ── Step 4 浏览器 AI 轮次耗时与 Token (KEEP-05 联动) ──
            s4_ai_round = {}
            br_rows = conn.execute(
                "SELECT metric_name, SUM(value) as total_val, COUNT(*) as cnt "
                "FROM ai_health_metrics WHERE project=%s AND run_id=%s AND step=4 "
                "AND metric_type='browser_ai_round' GROUP BY metric_name",
                (project, run_id),
            ).fetchall()
            for br in br_rows:
                mn = br["metric_name"]
                if mn == "elapsed_sec":
                    s4_ai_round["total_elapsed_sec"] = round(
                        float(br["total_val"] or 0), 1
                    )
                    s4_ai_round["round_count"] = int(br["cnt"] or 0)
                    s4_ai_round["avg_elapsed_sec"] = round(
                        float(br["total_val"] or 0) / max(int(br["cnt"] or 0), 1), 2
                    )
                elif mn == "prompt_tokens":
                    s4_ai_round["total_prompt_tokens"] = int(br["total_val"] or 0)
                elif mn == "completion_tokens":
                    s4_ai_round["total_completion_tokens"] = int(br["total_val"] or 0)

            # ── 总健康度评分（加权平均） —— 使用 fetchers._format
            overall_score = _build_health_score(
                s2,
                s3,
                s4_action,
                s4_verify,
                flaky_data,
                cache_data,
                case_dominance,
            )

            # ── 置信度映射 —— 使用 fetchers._format
            confidence = _build_labels(ACCURACY_LABELS, ACCURACY_LABEL_TEXT)

            # ── 运行统计 ──
            run_count = conn.execute(
                "SELECT COUNT(DISTINCT run_id) AS cnt FROM ai_health_metrics WHERE project=%s",
                (project,),
            ).fetchone()["cnt"]

        finally:
            _t_total = _time.time() - _t0
            _log.info(
                "[health-perf] 总耗时 %.2fs (conn获取 %.2fs, SQL合计 %.2fs)",
                _t_total,
                _t_conn,
                _t_total - _t_conn,
            )
            if conn:
                # ★ SUP-016: 连接池模式，close 仅释放回池不真正关闭
                try:
                    conn.close()
                except Exception as _e:
                    _log.debug(
                        "[health/summary] conn.close 失败(连接池模式): %s",
                        _e,
                        exc_info=True,
                    )
        # ★ 检查是否有健康度指标持久化错误
        flush_error = ""
        with guard("api_health_summary: from src.backend.state import ( _hea", log=_log, level=logging.WARNING):
            from src.backend.state import (
                _health_flush_errors,
                _health_flush_errors_lock,
            )

            with _health_flush_errors_lock:
                flush_error = _health_flush_errors.pop(run_id, "") or ""

        return jsonify(
            {
                "ok": True,
                "has_data": True,
                "overall_score": overall_score,
                "run_id": run_id,
                "run_count": run_count,
                "step_2": s2,
                "step_3": s3,
                "step_3_quality": s3_quality,
                "step_4_action": s4_action,
                "step_4_verify": s4_verify,
                "run_summary": run_summary,  # ★ 用例级执行结果（校验达成率数据源）
                "step_4_cache": cache_data,
                "step_4_ai_round": s4_ai_round,
                "step_4_case_dominance": case_dominance,
                "flaky": flaky_data,
                "confidence": confidence,
                "flush_error": flush_error,  # ★ 指标持久化失败信息
                "embedding": _get_embedding_status(),  # ★ RAG 语义引擎状态（前端可见）
            }
        )
    except Exception as e:
        _log.error("[api_health_summary] 异常: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": "服务器内部错误"}), 500
