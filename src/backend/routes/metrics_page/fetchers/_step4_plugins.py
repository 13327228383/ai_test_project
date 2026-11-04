# -*- coding: utf-8 -*-
"""
Step4 基础插件获取器（复用 _plugin.py 的 collect_plugin_metrics 顶层函数）
"""

import logging

_log = logging.getLogger(__name__)


def _fetch_step4_plugins(conn=None, run_id=None) -> dict:
    """直接调 collect_plugin_metrics(project)，绕开 HTTP 层。

    【修复】拆分后 api_plugin_metrics 被包进了 register(bp) 闭包，
    模块顶层不可见。改为复用同模块新提取的 collect_plugin_metrics(project)。

    可选传入共享 conn / run_id 减少 ai_health_metrics 重复查询。
    collect_plugin_metrics 内部自管连接（公共函数，不改）。
    """
    try:
        from src.backend.routes.routes._plugin import collect_plugin_metrics
        from src.backend.routes._data_helpers import _current_project
        from ._utils import (
            _db_conn,
            _current_version_id,
            _num,
            latest_run_id_for_metrics,
        )

        project = _current_project()
        if not project:
            return {"note": "未选择项目"}

        data = collect_plugin_metrics(project)
        if not data or not data.get("ok"):
            return {"note": data.get("error") or "plugin_metrics 无数据"}

        gt_total = data.get("ground_truth_total", 0)
        gt_fail = data.get("ground_truth_fail", 0)
        sh_total = data.get("self_heal_total", 0)
        sh_success = data.get("self_heal_success", 0)
        fcp_avg = data.get("fcp_avg", 0)

        # --- 成功用例步骤数（从 ai_health_metrics 读取） ---
        avg_step_count = None
        # guard_reject（守卫拦截率）
        guard_reject_count = None
        action_attempt_count = None
        guard_reject_rate = None
        # verification（三轨校验不一致率）
        verification_disagreement = None
        verification_total = None
        verification_disagree_rate = None
        # case_dominance（用例主导度）
        dominance_score = None
        recipe_only_pass = None
        case_total = None
        recipe_only_pass_rate = None
        _owns_conn = conn is None
        if _owns_conn:
            conn = _db_conn()
        if conn:
            try:
                if run_id is None:
                    version_id = _current_version_id()
                    # ★ 优先选同时有 guard_reject + action_attempt 的最新 run
                    # 之前 latest_run_id_for_metrics 只看 metric_type 存在，不验证两者同时有值
                    # 导致 Smartshop__guard_real（只有 guard_rejection=3，缺 action_attempt=分母）被选中
                    # 拦截率无法计算（分母为0/缺失）
                    candidate_run_ids = []
                    try:
                        # 查所有有 guard_reject 或 action_attempt 的 run
                        placeholders = ", ".join(["%s"] * 2)
                        rows = conn.execute(
                            f"SELECT run_id, "
                            f"  MAX(CASE WHEN metric_type='guard_reject' THEN 1 ELSE 0 END) as has_gr, "
                            f"  MAX(CASE WHEN metric_type='action_attempt' THEN 1 ELSE 0 END) as has_aa "
                            f"FROM ai_health_metrics "
                            f"WHERE project=%s AND metric_type IN ({placeholders}) AND step=4 "
                            f"GROUP BY run_id "
                            f"ORDER BY MAX(created_at) DESC "
                            f"LIMIT 20",
                            (project, "guard_reject", "action_attempt"),
                        ).fetchall()
                        # 优先选同时有 gr 和 aa 的
                        for r in rows:
                            if r["has_gr"] and r["has_aa"]:
                                run_id = r["run_id"]
                                break
                        # 如果没找到同时有的，再退回 latest_run_id_for_metrics 通用逻辑
                        if not run_id:
                            run_id = latest_run_id_for_metrics(
                                conn,
                                project,
                                version_id,
                                [
                                    "case_complexity",
                                    "guard_reject",
                                    "action_attempt",
                                    "verification",
                                    "case_dominance",
                                ],
                            )
                    except Exception as _e:
                        _log.warning("[step4_plugins] run_id 智能选择异常，退回通用逻辑: %s", _e)
                        run_id = latest_run_id_for_metrics(
                            conn,
                            project,
                            version_id,
                            [
                                "case_complexity",
                                "guard_reject",
                                "action_attempt",
                                "verification",
                                "case_dominance",
                            ],
                        )
                if run_id:
                    # 步骤数平均值
                    step_row = conn.execute(
                        "SELECT AVG(value) as avg_val FROM ai_health_metrics "
                        "WHERE project=%s AND run_id=%s AND step=4 AND metric_type='case_complexity' AND metric_name='step_count'",
                        (project, run_id),
                    ).fetchone()
                    if step_row and step_row["avg_val"] is not None:
                        avg_step_count = round(_num(step_row["avg_val"], 0), 1)

                    # guard_reject（守卫拦截率）= guard_reject / action_attempt * 100
                    # 写入端 health.py:570-575 写 metric_name='guard_rejection', category=reason_category（whitelist/missing_ai_id/...）
                    # 之前 fetcher 查 ('danger','total') 是错的——那是 harness_governance 的字段，不是 guard_reject 的
                    gr_row = conn.execute(
                        "SELECT "
                        "  SUM(CASE WHEN metric_type='guard_reject' AND metric_name='guard_rejection' THEN value END) as reject_cnt, "
                        "  SUM(CASE WHEN metric_type='action_attempt' THEN value END) as attempt_cnt "
                        "FROM ai_health_metrics "
                        "WHERE project=%s AND run_id=%s AND step=4 "
                        "  AND metric_type IN ('guard_reject', 'action_attempt')",
                        (project, run_id),
                    ).fetchone()
                    if gr_row:
                        # reject_cnt=0 且 DB 有 guard_reject 记录 → 真没触发拦截
                        # 给 None 而不是 0（避免误导性 0%）
                        has_gr_data = (
                            gr_row["reject_cnt"] is not None
                            and gr_row["reject_cnt"] > 0
                        )
                        if has_gr_data:
                            guard_reject_count = int(_num(gr_row["reject_cnt"], 0) or 0)
                            if gr_row["attempt_cnt"] is not None and gr_row["attempt_cnt"] > 0:
                                action_attempt_count = int(_num(gr_row["attempt_cnt"], 0) or 0)
                                guard_reject_rate = round(
                                    guard_reject_count / action_attempt_count * 100, 2,
                                )
                        else:
                            # 分母（动作尝试）已测量而拦截为零 → 真 0，与 routes_summary 口径一致；
                            # 仅在分母也未测量时保持"未采集"（前端显示 —）
                            if gr_row["attempt_cnt"] is not None and gr_row["attempt_cnt"] > 0:
                                guard_reject_count = 0
                                action_attempt_count = int(
                                    _num(gr_row["attempt_cnt"], 0) or 0
                                )
                                guard_reject_rate = 0.0
                            # 否则分母未测量 → 未采集态（前端显示 —）

                    # verification（三轨校验不一致率）= disagreement / total * 100
                    ver_row = conn.execute(
                        "SELECT "
                        "  SUM(CASE WHEN metric_name='disagreement' THEN value END) as disagree_cnt, "
                        "  COUNT(*) as total_cnt "
                        "FROM ai_health_metrics "
                        "WHERE project=%s AND run_id=%s AND step=4 AND metric_type='verification'",
                        (project, run_id),
                    ).fetchone()
                    if ver_row:
                        if ver_row["disagree_cnt"] is not None:
                            verification_disagreement = int(
                                _num(ver_row["disagree_cnt"], 0) or 0
                            )
                        verification_total = int(_num(ver_row["total_cnt"], 0) or 0)
                        if (
                            verification_total > 0
                            and verification_disagreement is not None
                        ):
                            verification_disagree_rate = round(
                                verification_disagreement / verification_total * 100, 2
                            )

                    # case_dominance（用例主导度）— recipe_only_pass率 = recipe_only_pass / case_total * 100
                    dom_row = conn.execute(
                        "SELECT "
                        "  AVG(CASE WHEN metric_name='dominance_score' THEN value END) as dominance_avg, "
                        "  SUM(CASE WHEN metric_name='recipe_only_pass' THEN value END) as recipe_only, "
                        "  SUM(CASE WHEN metric_name='case_total' THEN value END) as case_ttl "
                        "FROM ai_health_metrics "
                        "WHERE project=%s AND run_id=%s AND step=4 AND metric_type='case_dominance'",
                        (project, run_id),
                    ).fetchone()
                    if dom_row:
                        if dom_row["dominance_avg"] is not None:
                            dominance_score = round(
                                _num(dom_row["dominance_avg"], 0), 2
                            )
                        if dom_row["recipe_only"] is not None:
                            recipe_only_pass = int(_num(dom_row["recipe_only"], 0) or 0)
                        if dom_row["case_ttl"] is not None:
                            case_total = int(_num(dom_row["case_ttl"], 0) or 0)
                        if case_total is not None and case_total > 0:
                            recipe_only_pass_rate = round(
                                (recipe_only_pass or 0) / case_total * 100, 2
                            )
            except Exception as _e:
                _log.debug("[metrics] step4 指标读取异常: %s", _e)
            finally:
                if _owns_conn:
                    try:
                        conn.close()
                    except Exception as _e:
                        _log.debug("conn.close 失败: %s", _e, exc_info=True)

        # ★ 从 executions 表计算用例级/步骤级占比（按版本隔离，复用共享 conn）
        # ★ 根因修复(2026-09-21)：原口径 = PASS行数 / executions全部行数，把
        #   NOT_EXECUTED 占位行、PRE_GATE 行全算进分母（api_demo 实测 13 PASS/130 行
        #   = 10%，而用例级真实口径 9 PASS/9 终态 = 100%）——与 chart-data 的用例级
        #   口径互相打架，同一张 KPI 卡先渲染 100% 再被后台增强重绘成 10%。
        #   正确语义（与 get_execution_status_map/chart-data 对齐）：每 case 取最新
        #   一行状态；通过率 = (PASS+FLAKY) / (PASS+FAIL+FLAKY)，非终态不入分母。
        case_pass_rate = None
        case_total_count = None
        case_pass_count = None
        step_pass_rate = None
        step_total_count = None
        step_pass_count = None
        try:
            if conn:
                version_id = _current_version_id()
                if version_id:
                    _execs = conn.execute(
                        """
                        SELECT e.status AS status, COUNT(*) AS cnt
                        FROM executions e
                        JOIN (
                            SELECT MAX(id) AS mid FROM executions
                            WHERE project=%s AND version_id=%s GROUP BY case_id
                        ) t ON e.id = t.mid
                        GROUP BY e.status
                        """,
                        (project, version_id),
                    ).fetchall()
                else:
                    _execs = conn.execute(
                        """
                        SELECT e.status AS status, COUNT(*) AS cnt
                        FROM executions e
                        JOIN (
                            SELECT MAX(id) AS mid FROM executions
                            WHERE project=%s AND version_id=0 GROUP BY case_id
                        ) t ON e.id = t.mid
                        GROUP BY e.status
                        """,
                        (project,),
                    ).fetchall()
                if _execs:
                    _exec_map = {r["status"]: _num(r["cnt"], 0) for r in _execs}
                    _case_pass = (_exec_map.get("PASS", 0)
                                  + _exec_map.get("FLAKY", 0))
                    _case_fail = _exec_map.get("FAIL", 0)
                    _terminal = (_case_pass + _case_fail
                                 + _exec_map.get("SKIPPED", 0)
                                 + _exec_map.get("SKIP", 0)
                                 + _exec_map.get("BLOCKED", 0))
                    if _terminal > 0:
                        case_pass_rate = round(_case_pass / _terminal * 100, 1)
                    case_total_count = _terminal or None
                    case_pass_count = _case_pass
                    # 步骤级：从 case_complexity 估算总步骤数（基于终态用例数）
                    _avg_steps = avg_step_count or 0
                    if _avg_steps > 0 and case_total_count:
                        _case_total = case_total_count
                        _step_total = _case_total * _avg_steps
                        _step_pass = _case_pass * _avg_steps
                        _step_fail = (_case_total - _case_pass) * _avg_steps * 0.5
                        _step_total_est = _step_total
                        _step_pass_est = _step_pass + _step_fail
                        if _step_total_est > 0:
                            step_pass_rate = round(
                                _step_pass_est / _step_total_est * 100, 1
                            )
                        step_total_count = int(_step_total_est)
                        step_pass_count = int(_step_pass_est)
        except Exception as _e:
            _log.debug("[step4_plugins] 估算步骤指标异常: %s", _e, exc_info=True)

        return {
            # ★ 未采集（total=0）时返回 None，前端显示 "—" 而非误导性的 "0%"
            "gt_intercept_rate": (
                round(gt_fail / gt_total * 100, 2) if gt_total > 0 else None
            ),
            "gt_total": gt_total,
            "visual_baseline_status": data.get("visual_baseline_status", "unknown"),
            "self_heal_success_rate": (
                round(sh_success / sh_total * 100, 2) if sh_total > 0 else None
            ),
            "self_heal_total": sh_total,
            "fcp_avg_ms": fcp_avg if fcp_avg and fcp_avg > 0 else None,
            "plugins_registered": data.get("plugins", []),
            "avg_step_count": avg_step_count,
            # guard_reject（守卫拦截率）
            "guard_reject_count": guard_reject_count,
            "action_attempt_count": action_attempt_count,
            "guard_reject_rate": guard_reject_rate,
            # verification（三轨校验不一致率）
            "verification_disagreement": verification_disagreement,
            "verification_total": verification_total,
            "verification_disagree_rate": verification_disagree_rate,
            # case_dominance（用例主导度）
            "dominance_score": dominance_score,
            "recipe_only_pass": recipe_only_pass,
            "case_total": case_total,
            "recipe_only_pass_rate": recipe_only_pass_rate,
            # ★ 用例级/步骤级占比
            "case_pass_rate": case_pass_rate,
            "case_total_count": case_total_count,
            "case_pass_count": case_pass_count,
            "step_pass_rate": step_pass_rate,
            "step_total_count": step_total_count,
            "step_pass_count": step_pass_count,
        }
    except Exception as e:
        _log.warning("[metrics] _fetch_step4_plugins 异常: %s", e)
        return {"note": f"读取异常: {e}"}
