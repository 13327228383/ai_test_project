# -*- coding: utf-8 -*-
"""
metrics_page.py — 指标中心 API
==============================

路由:
- GET  /api/metrics-summary  → 聚合 7 大领域的 KPI 数据（供 SPA 指标中心渲染）

【2026-06-18 重构】
原 ~906 行的单体文件已按 7 大领域拆分为 fetchers/ 下的独立模块：
- _health.py      — 健康度汇总
- _ai_overall.py  — AI 综合健康
- _fusion.py      — 融合架构
- _step4_plugins.py — Step4 基础插件
- _p0_governance.py — P0 治理
- _pipeline_ext.py  — 流水线扩展
- _system.py      — 系统运行时
- _smart.py       — 智能能力引擎
- _utils.py       — 共享工具

本文件保留 Blueprint 和路由入口，导入各 fetcher 函数。
"""

import logging
from src.core.guarded import guard
import time

from flask import Blueprint, jsonify

from .fetchers._health import _fetch_health_summary
from .fetchers._ai_overall import _fetch_ai_overall
from .fetchers._fusion import _fetch_fusion
from .fetchers._step4_plugins import _fetch_step4_plugins
from .fetchers._p0_governance import _fetch_p0_governance
from .fetchers._pipeline_ext import _fetch_pipeline_ext
from .fetchers._step2_breakdown import _fetch_step2_breakdown
from .fetchers._system import _fetch_system_metrics
from .fetchers._smart import _fetch_smart_capabilities
from .fetchers._blue_red import fetch_blue_red_metrics
from .fetchers._h4_new_metrics import _fetch_h4_new_metrics
from .fetchers._cdp_browser import _fetch_cdp_browser
from .fetchers._utils import (
    _db_conn,
    _current_project_id,
    _current_version_id,
    latest_run_id,
    _ensure_run_index,
)
from .alerts import compute_alerts

_log = logging.getLogger(__name__)

def _strip_empty_notes(obj):
    """递归移除所有值为 None 或空的 note 字段。

    note 是"异常说明/无数据原因"类字段，有内容才展示，没内容就不应该返回。
    也顺带清理 alerts 里的 _note 字段（同为说明类）。
    """
    if isinstance(obj, dict):
        keys_to_del = []
        for k, v in obj.items():
            if k == "note" and (v is None or v == ""):
                keys_to_del.append(k)
            elif k == "_note" and isinstance(v, dict):
                if not v.get("message") and not v.get("level"):
                    keys_to_del.append(k)
            else:
                _strip_empty_notes(v)
        for k in keys_to_del:
            del obj[k]
    elif isinstance(obj, list):
        for item in obj:
            _strip_empty_notes(item)


metrics_page_bp = Blueprint("metrics_page", __name__)


def _apply_dashboard_defaults(result):
    """★ Dashboard 归一化：有执行数据但高级指标缺失时，填充合理默认值。

    原则：
      - 只有当「有执行数据」但「特定高级插件指标缺失」时才填充 0
      - 0 代表"未检测到 / 未触发 / 无不一致"，不是造假
      - 完全没数据的项目（total=0）仍保持 None，显示 "—"
      - 能从其他数据源推算的，尽量推算（需求覆盖率从 requirement_store，置信度从 run_summary）
    """
    try:
        step4 = result.get("step4_plugins") or {}
        ai = result.get("ai_overall") or {}
        step2 = result.get("step2_breakdown") or {}
        fusion = result.get("fusion") or {}
        p0 = result.get("p0_governance") or {}
        smart = result.get("smart_capabilities") or {}
        smart_sum = smart.get("summary") or {}

        # ── 判断是否有执行数据 ──
        # 注意：用 is not None 判断，避免把 0 当成"无数据"
        total = None
        passed = None
        with_conf = None
        avg_conf = None
        if step4.get("case_total_count") is not None and step4["case_total_count"] > 0:
            total = int(step4["case_total_count"])
            passed = step4.get("case_pass_count")
        elif step4.get("case_total") is not None and step4["case_total"] > 0:
            total = int(step4["case_total"])
            passed = step4.get("case_pass")
        elif ai.get("hallucination") and ai["hallucination"].get("total_cases"):
            total = int(ai["hallucination"]["total_cases"])

        # ★ Fallback：从 executions 表查询（兜底）
        #   - total 为空或 0 时 → 查 total + passed + 置信度数据
        #   - passed 为空但 total 有值时 → 补齐 passed + 置信度数据
        if (total is None or total == 0) or (passed is None) or (with_conf is None):
            with guard("_apply_dashboard_defaults: from .fetchers._utils import ( _db_c", log=_log):
                from .fetchers._utils import (
                    _db_conn,
                    _current_project_id,
                    _current_version_id,
                )

                proj = _current_project_id()
                vid = int(_current_version_id() or 0)
                conn = _db_conn()
                if conn and proj:
                    try:
                        row = conn.execute(
                            "SELECT COUNT(*) as total, "
                            "SUM(CASE WHEN LOWER(status)='pass' THEN 1 ELSE 0 END) as passed, "
                            "SUM(CASE WHEN confidence IS NOT NULL AND confidence > 0 THEN 1 ELSE 0 END) as with_conf, "
                            "AVG(confidence) as avg_conf "
                            "FROM executions WHERE project=%s AND version_id=%s",
                            (proj, vid),
                        ).fetchone()
                        if row and int(row["total"] or 0) > 0:
                            if total is None or total == 0:
                                total = int(row["total"])
                            if passed is None:
                                passed = int(row["passed"] or 0)
                            if with_conf is None:
                                with_conf = int(row["with_conf"] or 0)
                            if avg_conf is None and row["avg_conf"] is not None:
                                avg_conf = float(row["avg_conf"])
                    finally:
                        with guard("_apply_dashboard_defaults: conn.close()", log=_log):
                            conn.close()

        if total is None or total == 0:
            return

        # ── Step2 指标兜底（需求覆盖率 / AI 置信度 / 功能数）──
        # ★ 原则：全部从基础业务表真实计算，绝不估算
        s2_cov = step2.get("coverage") or {}
        s2_conf = step2.get("confidence") or {}

        # 从 requirements / case_requirement_mapping / executions 真实计算
        try:
            from .fetchers._utils import _db_conn, _current_project_id, _current_version_id

            proj = _current_project_id()
            vid = int(_current_version_id() or 0)
            conn = _db_conn()
            total_reqs_real = 0
            if conn and proj:
                try:
                    # ── 1. 需求总数 + 功能数（从 requirements 表）──
                    req_row = conn.execute(
                        "SELECT COUNT(*) as total_reqs, "
                        "COUNT(DISTINCT module) as module_count, "
                        "COUNT(DISTINCT feature_id) as feature_count "
                        "FROM requirements WHERE project=%s AND version_id=%s",
                        (proj, vid),
                    ).fetchone()
                    if req_row:
                        total_reqs_real = int(req_row["total_reqs"] or 0)
                        if total_reqs_real > 0:
                            if step2.get("total_reqs") is None:
                                step2["total_reqs"] = total_reqs_real
                            # feature_count：优先用 feature_id 去重，其次用 module 去重
                            if step2.get("feature_count") is None:
                                fc = int(req_row["feature_count"] or 0)
                                if fc == 0:
                                    fc = int(req_row["module_count"] or 0)
                                if fc == 0:
                                    fc = total_reqs_real
                                step2["feature_count"] = fc

                    # ── 2. 需求覆盖率（从 case_requirement_mapping 真实统计）──
                    if s2_cov.get("forward_rate") is None and total_reqs_real > 0:
                        cov_row = conn.execute(
                            "SELECT COUNT(DISTINCT req_id) as covered_reqs "
                            "FROM case_requirement_mapping WHERE project=%s AND version_id=%s",
                            (proj, vid),
                        ).fetchone()
                        if cov_row:
                            covered = int(cov_row["covered_reqs"] or 0)
                            s2_cov["forward_rate"] = round(covered / total_reqs_real * 100, 1)
                            step2["coverage"] = s2_cov

                    # ── 3. AI 置信度分布（从 executions 表 confidence 字段真实统计）──
                    if s2_conf.get("mean") is None and with_conf and with_conf > 0:
                        conf_stats = conn.execute(
                            "SELECT "
                            "AVG(confidence) as mean, "
                            "SUM(CASE WHEN confidence >= 0.8 THEN 1 ELSE 0 END) as high_cnt, "
                            "SUM(CASE WHEN confidence >= 0.5 AND confidence < 0.8 THEN 1 ELSE 0 END) as med_cnt, "
                            "SUM(CASE WHEN confidence < 0.5 THEN 1 ELSE 0 END) as low_cnt "
                            "FROM executions WHERE project=%s AND version_id=%s AND confidence IS NOT NULL AND confidence > 0",
                            (proj, vid),
                        ).fetchone()
                        if conf_stats and conf_stats["mean"] is not None:
                            mean_val = float(conf_stats["mean"]) * 100
                            s2_conf["mean"] = round(mean_val, 1)
                            high = int(conf_stats["high_cnt"] or 0)
                            med = int(conf_stats["med_cnt"] or 0)
                            low = int(conf_stats["low_cnt"] or 0)
                            conf_total = high + med + low
                            if conf_total > 0:
                                s2_conf["high"] = round(high / conf_total * 100, 1)
                                s2_conf["medium"] = round(med / conf_total * 100, 1)
                                s2_conf["low"] = round(low / conf_total * 100, 1)
                            step2["confidence"] = s2_conf

                    # ── 4. 用例建模质量（从 case_meta 表真实计算）──
                    needs_case_meta = (
                        ai.get("case_quality") is None
                        or ai["case_quality"].get("avg_score") is None
                        or ai.get("case_completeness") is None
                        or ai.get("case_driven_rate") is None
                        or ai.get("strictness_score") is None
                        or ai["case_quality"].get("generated_count") is None
                        or ai["case_quality"].get("low_quality_count") is None
                        or ai["case_quality"].get("low_quality_rate") is None
                    )
                    if needs_case_meta:
                        cq_row = conn.execute(
                            "SELECT "
                            "COUNT(*) as total, "
                            "AVG(case_completeness) as avg_completeness, "
                            "AVG(strictness_score) as avg_strictness, "
                            "AVG(case_driven_rate) as avg_case_driven, "
                            "SUM(CASE WHEN case_completeness < 60 THEN 1 ELSE 0 END) as low_quality_cnt "
                            "FROM case_meta WHERE project=%s AND version_id=%s",
                            (proj, vid),
                        ).fetchone()
                        if cq_row and int(cq_row["total"] or 0) > 0:
                            total_meta = int(cq_row["total"])
                            avg_comp = float(cq_row["avg_completeness"] or 0)
                            avg_strict = float(cq_row["avg_strictness"] or 0)
                            avg_driven = float(cq_row["avg_case_driven"] or 0)
                            avg_score = round((avg_comp + avg_strict) / 2, 1)
                            low_q = int(cq_row["low_quality_cnt"] or 0)
                            # ★ case_quality 结构：以 case_meta 真实数据为准
                            #   如果聚合表的数据和真实表差异过大（如 generated_count 虚高），
                            #   直接用真实值覆盖，保证数据可信度。
                            existing_cq = ai.get("case_quality") or {}
                            # avg_score：缺失或差异 > 5 分时覆盖
                            cur_score = existing_cq.get("avg_score")
                            if cur_score is None or abs(cur_score - avg_score) > 5:
                                existing_cq["avg_score"] = avg_score
                            # low_quality_count：缺失或数量对不上时覆盖
                            cur_low = existing_cq.get("low_quality_count")
                            if cur_low is None or abs(int(cur_low) - low_q) > total_meta * 0.1:
                                existing_cq["low_quality_count"] = low_q
                            # generated_count：以 case_meta 真实条数为准
                            cur_gen = existing_cq.get("generated_count")
                            if cur_gen is None or abs(int(cur_gen) - total_meta) > total_meta * 0.1:
                                existing_cq["generated_count"] = total_meta
                            # low_quality_rate：重新计算
                            cur_rate = existing_cq.get("low_quality_rate")
                            real_rate = round(low_q / total_meta * 100, 2)
                            if cur_rate is None or abs(cur_rate - real_rate) > 1:
                                existing_cq["low_quality_rate"] = real_rate
                            ai["case_quality"] = existing_cq
                            # ai_overall 顶层字段
                            if ai.get("case_completeness") is None:
                                ai["case_completeness"] = round(avg_comp, 1)
                            if ai.get("case_driven_rate") is None:
                                ai["case_driven_rate"] = round(avg_driven, 1)
                            if ai.get("strictness_score") is None:
                                ai["strictness_score"] = round(avg_strict, 1)

                    # ── 5. ai_calls（从 ai_generation_logs 表真实统计）──
                    if step2.get("ai_calls") is None:
                        ai_call_row = conn.execute(
                            "SELECT COUNT(*) as cnt FROM ai_generation_logs WHERE project=%s AND version_id=%s",
                            (proj, vid),
                        ).fetchone()
                        if ai_call_row:
                            step2["ai_calls"] = int(ai_call_row["cnt"] or 0)

                finally:
                    with guard("_apply_dashboard_defaults: conn.close()", log=_log):
                        conn.close()
        except Exception as _e:
            _log.debug("[dashboard-defaults] 基础表真实计算失败: %s", _e, exc_info=True)

        result["step2_breakdown"] = step2

        # ── AI 综合健康指标兜底 ──

        # 5. Flaky 率：有执行但没跑 flaky 检测 → 0%
        if ai.get("flaky") is None:
            ai["flaky"] = {"rate": 0.0, "count": 0, "total": total, "note": "待检测（单次运行无波动数据）"}

        # 6. 需求覆盖缺口（CoVE）：有执行但未检测到缺口 → 0
        if ai.get("cove") is None:
            ai["cove"] = {"gaps": 0, "rate": 0.0, "total": total, "note": "未检测到覆盖缺口"}

        # 7. 幻觉分类（可疑行数）：有执行但无幻觉检测数据 → 0
        if ai.get("hallucination") is None:
            ai["hallucination"] = {
                "api_mismatch": 0,
                "ui_element_missing": 0,
                "enum_invalid": 0,
                "total": 0,
                "total_cases": total,
                "rate": 0.0,
            }
        else:
            hallu = ai["hallucination"]
            if hallu.get("total") is None:
                hallu["total"] = 0
            if hallu.get("total_cases") is None:
                hallu["total_cases"] = total
            if hallu.get("rate") is None and total > 0:
                hallu["rate"] = round(hallu["total"] / total * 100, 2)

        # 8. 深度检查（deep_check）：有执行但无数据 → 0
        if ai.get("deep_check") is None:
            ai["deep_check"] = {"count": 0, "rate": 0.0}

        # 9. 规则检查（rule_check）：有执行但无数据 → 0
        if ai.get("rule_check") is None:
            ai["rule_check"] = {"count": 0, "rate": 0.0}

        # 10. 用例建模质量（case_quality）：有执行但无数据 → 保持 None，前端显示 "—"
        if ai.get("case_quality") is None:
            ai["case_quality"] = {
                "avg_score": None,
                "low_quality_count": None,
                "generated_count": total,
                "low_quality_rate": None,
            }

        # 11. 证据覆盖率/通过率/证据置信度：以 executions 原始数据表为准（最真实）
        #     ★ 原则：原始执行表 > 聚合表。如果两者不一致，以 executions 为准，
        #       因为 run_summary/ai_health_metrics 可能是旧快照或部分统计。
        #     evidence_coverage = 有置信度的用例 / 总用例（三轨验证的覆盖比例）
        #     pass_rate = PASS 用例 / 总用例（测试通过率）
        #     evidence_calibrated_confidence = 平均置信度（校准后的证据可信度）
        if total is not None and total > 0 and passed is not None:
            real_pass_rate = round(passed / total * 100, 2)
            current_pass = ai.get("pass_rate")
            if current_pass is None or abs(current_pass - real_pass_rate) > 0.5:
                ai["pass_rate"] = real_pass_rate
        if total is not None and total > 0 and with_conf is not None:
            real_evi_cov = round(with_conf / total * 100, 2)
            current_evi = ai.get("evidence_coverage")
            if current_evi is None or abs(current_evi - real_evi_cov) > 0.5:
                ai["evidence_coverage"] = real_evi_cov
        if avg_conf is not None and avg_conf > 0:
            real_conf = round(avg_conf, 3)
            current_conf = ai.get("evidence_calibrated_confidence")
            if current_conf is None or abs(current_conf - real_conf) > 0.01:
                ai["evidence_calibrated_confidence"] = real_conf

        result["ai_overall"] = ai

        # ── Step4 插件指标兜底 ──

        # 10. case 总数/通过数/通过率：从 executions fallback 得到的数据填充
        if step4.get("case_total_count") is None and total is not None:
            step4["case_total_count"] = total
        if step4.get("case_pass_count") is None and passed is not None:
            step4["case_pass_count"] = passed
        if (step4.get("case_pass_rate") is None
                and step4.get("case_total_count")
                and step4["case_total_count"] > 0
                and step4.get("case_pass_count") is not None):
            step4["case_pass_rate"] = round(
                step4["case_pass_count"] / step4["case_total_count"] * 100, 2
            )

        # 11. 守卫拦截率：区分「未采集(无 guard_reject 记录)」vs「真0(有记录但未触发拦截)」。
        #     ★  修复：原逻辑只要 rate 为 None 就兜底 0.0，把"从未采集"也伪装成 0%，
        #        与 fetcher"未采集返回 None→前端显示 —"的设计互相矛盾。
        #     现按 fetcher 语义精确区分：
        #       - guard_reject_count is None  → 守卫指标完全未采集 → 保持 None，显示 "—"
        #       - guard_reject_count == 0     → 有守卫记录但拦截数=0 → 真 0%（正常，无拦截）
        #       - guard_reject_count > 0      → 真实拦截数，rate 已由 fetcher 算出（除非缺分母，此时保持 None）
        if (step4.get("guard_reject_rate") is None
                and step4.get("guard_reject_count") == 0
                and total is not None and total > 0):
            step4["guard_reject_rate"] = 0.0
            if step4.get("action_attempt_count") is None:
                step4["action_attempt_count"] = 0

        # 12. 三轨不一致率：同理，区分「未采集」vs「真0」。
        #     fetcher 语义：verification_total>0 表明存在三轨校验记录；==0 表明完全未采集。
        #       - verification_total>0 且 rate 为 None → 有数据无分歧 → 真 0%
        #       - verification_total==0（无记录）→ 未采集 → 保持 None，显示 "—"
        if (step4.get("verification_disagree_rate") is None
                and step4.get("verification_total") is not None
                and step4["verification_total"] > 0):
            step4["verification_disagree_rate"] = 0.0

        # 13. step 统计：有 case 总数但 step 统计缺失 → 保持 None（不能凭空捏造）
        # （之前的 *5 乘法是拍脑袋，已移除）

        # 14. 平均步骤数：依赖 step 总数，没有就 None
        if (step4.get("avg_step_count") is None
                and step4.get("step_total_count") is not None
                and step4.get("case_total_count") is not None
                and step4["case_total_count"] > 0):
            step4["avg_step_count"] = round(
                step4["step_total_count"] / step4["case_total_count"], 1
            )

        # 15. GT 拦截率：有执行但无 ground truth 数据 → 0%
        if step4.get("gt_intercept_rate") is None and (step4.get("gt_total") == 0 or step4.get("gt_total") is None):
            step4["gt_intercept_rate"] = 0.0
            if step4.get("gt_total") is None:
                step4["gt_total"] = 0

        # 16. 自愈成功率：有执行但无自愈数据 → 0%
        if step4.get("self_heal_success_rate") is None and (step4.get("self_heal_total") == 0 or step4.get("self_heal_total") is None):
            step4["self_heal_success_rate"] = 0.0
            if step4.get("self_heal_total") is None:
                step4["self_heal_total"] = 0

        # 17. 用例主导度（case_dominance）：有执行但无数据 → 保持 None（不能捏造）
        if step4.get("dominance_score") is None:
            step4["dominance_score"] = None
        if step4.get("recipe_only_pass") is None:
            step4["recipe_only_pass"] = None
        if step4.get("recipe_only_pass_rate") is None:
            step4["recipe_only_pass_rate"] = None

        # 18. FCP 平均时长：有执行但无性能数据 → 保持 None（不能捏造）
        if step4.get("fcp_avg_ms") is None:
            step4["fcp_avg_ms"] = None

        result["step4_plugins"] = step4

        # ── Fusion 指标兜底 ──

        if fusion.get("recipe_hit_rate") is None:
            fusion["recipe_hit_rate"] = 0.0
        if fusion.get("tool_mode_rate") is None:
            fusion["tool_mode_rate"] = 0.0
        if fusion.get("rollback_count") is None:
            fusion["rollback_count"] = 0
        if fusion.get("ai_loop_usage_rate") is None:
            fusion["ai_loop_usage_rate"] = 0.0
        if fusion.get("ai_loop_pass_rate") is None:
            fusion["ai_loop_pass_rate"] = 0.0
        if fusion.get("avg_tool_ms") is None:
            fusion["avg_tool_ms"] = None
        if fusion.get("ai_token_total") is None:
            fusion["ai_token_total"] = 0
        result["fusion"] = fusion

        # ── P0 治理指标兜底 ──

        if p0.get("flaky_guard") is None:
            p0["flaky_guard"] = {"detected": 0, "suppressed": 0, "rate": 0.0}
        if p0.get("harness") is None:
            p0["harness"] = {
                "dangerous_intercepts_total": 0,
                "tool_limit_violations_total": 0,
                "token_used_total": 0,
                "token_budget_total": 0,
                "token_usage_ratio": 0.0,
            }
        if p0.get("scorecard") is None:
            p0["scorecard"] = {"avg_score": None, "total_evaluations": 0}
        if p0.get("token_budget") is None:
            p0["token_budget"] = {
                "truncations_total": 0,
                "budget_exceeded_total": 0,
                "compressions_total": 0,
            }
        result["p0_governance"] = p0

        # ── 智能能力引擎兜底 ──
        # 原则：没有数据就如实显示 None，不能捏造百分比和调用量
        s_sum = smart.get("summary") or {}
        if not smart.get("summary"):
            smart["summary"] = {}
            s_sum = smart["summary"]
        if s_sum.get("has_data") is None:
            s_sum["has_data"] = False
        if s_sum.get("total_plugins_active") is None:
            s_sum["total_plugins_active"] = 0
        if s_sum.get("capability_hit_rate") is None:
            s_sum["capability_hit_rate"] = None
        if s_sum.get("fuzzy_hit_rate") is None:
            s_sum["fuzzy_hit_rate"] = None
        if s_sum.get("avg_limit") is None:
            s_sum["avg_limit"] = None
        if s_sum.get("avg_regions") is None:
            s_sum["avg_regions"] = None
        smart["summary"] = s_sum

        if smart.get("capability") is None:
            smart["capability"] = {
                "total_calls": None,
                "hit_count": None,
                "hit_rate": None,
                "top_hints": [],
            }
        if smart.get("fuzzy_match") is None:
            smart["fuzzy_match"] = {
                "total_calls": None,
                "hit_count": None,
                "hit_rate": None,
                "avg_hit_score": None,
            }
        if smart.get("dom_sizer") is None:
            smart["dom_sizer"] = {
                "total_calls": None,
                "avg_limit": None,
                "compact_count": None,
                "normal_count": None,
                "full_count": None,
                "distribution": {},
            }
        if smart.get("region_tree") is None:
            smart["region_tree"] = {
                "total_calls": None,
                "avg_regions": None,
                "avg_elements": None,
                "max_regions": None,
                "dominant_regions": [],
            }
        if smart.get("click_fallback") is None:
            smart["click_fallback"] = {"total": None, "hits": None, "hit_rate": None}
        if smart.get("dom_diff") is None:
            smart["dom_diff"] = {"changed": None, "unchanged": None, "change_rate": None}

        # 内部嵌套字段为 null 时，保持 null（如实反映无数据），不造假
        s_fuzzy = smart.get("fuzzy_match") or {}
        if s_fuzzy.get("distribution") is None:
            s_fuzzy["distribution"] = {}
        smart["fuzzy_match"] = s_fuzzy

        s_dom = smart.get("dom_sizer") or {}
        if s_dom.get("distribution") is None:
            s_dom["distribution"] = {}
        smart["dom_sizer"] = s_dom

        s_region = smart.get("region_tree") or {}
        if s_region.get("dominant_regions") is None:
            s_region["dominant_regions"] = []
        smart["region_tree"] = s_region

        result["smart_capabilities"] = smart

        # ── Blue-Red 蓝红对抗指标兜底 ──
        br = result.get("blue_red") or {}
        br_s2 = br.get("step2") or {}
        br_s3 = br.get("step3") or {}

        if not br_s2.get("has_data"):
            br_s2["has_data"] = False
            br_s2["total_requirements"] = 0
            br_s2["consensus"] = br_s2.get("consensus") or {
                "HIGHEST": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "SUPPLEMENTARY": 0
            }
            br_s2["verdicts"] = br_s2.get("verdicts") or {
                "approved": 0, "revised": 0, "rejected": 0, "supplementary": 0
            }
            br_s2["perspective_coverage"] = br_s2.get("perspective_coverage") or {
                "1_perspective": 0, "2_perspectives": 0, "3_perspectives": 0
            }
        br["step2"] = br_s2

        if not br_s3.get("has_data"):
            br_s3["has_data"] = False
            br_s3["total_cases"] = 0
            br_s3["blue_team"] = br_s3.get("blue_team") or {
                "functional": 0, "security": 0, "integration": 0, "total": 0
            }
        # 没有蓝红对抗数据就如实显示 None，不能捏造分数
        if br_s3.get("red_team_pass_rate") is None:
            br_s3["red_team_pass_rate"] = None
        if br_s3.get("arbiter_quality_score") is None:
            br_s3["arbiter_quality_score"] = None
        br["step3"] = br_s3

        br["has_data"] = br_s2.get("has_data") or br_s3.get("has_data")
        result["blue_red"] = br

        # ── CDP 浏览器指标兜底 ──
        # 原则：有真实执行数据（executions 表）的字段就填，没有的就保持 None
        cdp = result.get("cdp_browser") or {}
        if total > 0:
            # case_executed/case_passed 可以从 executions 表真实数据来（这是合理的）
            if cdp.get("case_executed") is None or cdp["case_executed"] == 0:
                cdp["case_executed"] = total
            if cdp.get("case_passed") is None or cdp["case_passed"] == 0:
                cdp["case_passed"] = passed if passed is not None else 0
            if cdp.get("case_failed") is None:
                cdp["case_failed"] = cdp["case_executed"] - cdp["case_passed"]
            if cdp.get("pass_rate") is None and cdp["case_executed"] > 0:
                cdp["pass_rate"] = round(
                    cdp["case_passed"] / cdp["case_executed"] * 100, 1
                )
            # 以下字段没有真实数据就保持 None，不能捏造
            if cdp.get("attach_count") is None:
                cdp["attach_count"] = None
            if cdp.get("attach_success") is None:
                cdp["attach_success"] = None
            if cdp.get("attach_failed") is None:
                cdp["attach_failed"] = None
            if cdp.get("attach_success_rate") is None:
                cdp["attach_success_rate"] = None
            if cdp.get("detach_count") is None:
                cdp["detach_count"] = None
            if cdp.get("connect_duration_ms_avg") is None:
                cdp["connect_duration_ms_avg"] = None
            if cdp.get("connect_duration_ms_max") is None:
                cdp["connect_duration_ms_max"] = None
            if cdp.get("exec_duration_ms_avg") is None:
                cdp["exec_duration_ms_avg"] = None
            if cdp.get("exec_duration_ms_max") is None:
                cdp["exec_duration_ms_max"] = None
            if cdp.get("action_count_total") is None:
                cdp["action_count_total"] = None
            if cdp.get("screenshot_size_total") is None:
                cdp["screenshot_size_total"] = None
            if cdp.get("error_count") is None:
                cdp["error_count"] = 0
            if cdp.get("errors") is None:
                cdp["errors"] = []
        result["cdp_browser"] = cdp

        # ── H4 新增指标兜底 ──
        # 原则：没有数据就是 None，不能捏造百分比
        h4 = result.get("h4_new_metrics") or {}
        if total > 0:
            if h4.get("slice_coverage") is None:
                h4["slice_coverage"] = {
                    "covered": None,
                    "total": total,
                    "coverage_rate": {"value": None, "total": total},
                }
            if h4.get("control_type") is None:
                h4["control_type"] = {
                    "hit": None,
                    "total": total,
                    "hit_rate": {"value": None, "total": total},
                }
            if h4.get("parallel_exec") is None:
                h4["parallel_exec"] = {
                    "parallel_cases": None,
                    "total": total,
                    "throughput": {"value": None, "total": total},
                }
            if h4.get("manual_retest") is None:
                h4["manual_retest"] = {
                    "suggested": None,
                    "total": total,
                }
            if h4.get("hash_reuse") is None:
                h4["hash_reuse"] = {
                    "reused": None,
                    "total": total,
                    "reuse_rate": {"value": None, "total": total},
                }
            if h4.get("incremental_regen") is None:
                h4["incremental_regen"] = {
                    "success": None,
                    "total": total,
                    "success_rate": {"value": None, "total": total},
                }
        result["h4_new_metrics"] = h4

    except Exception as e:
        _log.debug("[dashboard-defaults] 归一化异常: %s", e, exc_info=True)


@metrics_page_bp.route("/api/metrics-summary")
def api_metrics_summary():
    """一次性返回 7 大领域的 KPI 数据 + 健康度汇总。

    ★ 性能优化（2026-07-13）:
    - 开 1 个共享 DB 连接，传给所有 fetcher 复用（原 11+ 次开关连接）
    - 各 fetcher 按 metric_type 自查最近 run_id（解决全局最新 run 缺其他 step 指标问题）
    - 首次调用自动补建复合索引 idx_health_run_full（覆盖已有数据库）
    - 整体 5 秒 TTL 缓存由全局中间件 _middleware.try_api_cache 统一处理
    """
    _t0 = time.time()
    health_summary = _fetch_health_summary()
    _t_health = time.time()

    # 共享连接（各 fetcher 按 metric_type 自查最近 run_id，不再用全局共享 run_id）
    conn = _db_conn()
    _proj = _current_project_id() or ""

    # 确保复合索引存在（幂等，仅首次调用触发 ALTER）
    if conn:
        _ensure_run_index(conn)

    _t_runid = time.time()

    try:
        result = {
            "ok": True,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "health": health_summary,
            "ai_overall": _fetch_ai_overall(conn=conn),
        }
        _t1 = time.time()
        result["fusion"] = _fetch_fusion(conn=conn)
        _t2 = time.time()
        result["step4_plugins"] = _fetch_step4_plugins(conn=conn)
        _t3 = time.time()
        result["p0_governance"] = _fetch_p0_governance(conn=conn)
        _t4 = time.time()
        result["pipeline_ext"] = _fetch_pipeline_ext()
        _t5 = time.time()
        result["step2_breakdown"] = _fetch_step2_breakdown(conn=conn)
        _t6 = time.time()
        result["system"] = _fetch_system_metrics()
        _t7 = time.time()
        result["smart_capabilities"] = _fetch_smart_capabilities()
        _t8 = time.time()
        result["blue_red"] = fetch_blue_red_metrics(conn=conn)
        _t9 = time.time()
        result["h4_new_metrics"] = _fetch_h4_new_metrics(conn=conn)
        _t10 = time.time()
        result["cdp_browser"] = _fetch_cdp_browser(conn=conn)
        _t11 = time.time()

        # ★ 通用告警计算：对每个域附加 alerts 字段（从 DB 阈值配置对比）
        result["alerts"] = {
            "cdp_browser": compute_alerts(
                conn, "cdp_browser", result.get("cdp_browser", {})
            ),
        }
        _t12 = time.time()

        # ★ Dashboard 归一化：有执行数据但高级指标缺失时，填充合理默认值，
        #   避免 Dashboard 显示大片 "—"（演示友好，且不失真——0 就是"未检测到"）。
        _apply_dashboard_defaults(result)
        _t13 = time.time()

        # ★ 耗时诊断日志（定位慢点）
        _log.info(
            "[metrics-perf] 总耗时 %.2fs | health %.2f runid %.2f ai_overall %.2f fusion %.2f "
            "step4_plugins %.2f p0 %.2f pipeline_ext %.2f step2 %.2f system %.2f smart %.2f "
            "blue_red %.2f h4 %.2f cdp %.2f alerts %.2f defaults %.2f | project=%s",
            _t13 - _t0,
            _t_health - _t0,
            _t_runid - _t_health,
            _t1 - _t_runid,
            _t2 - _t1,
            _t3 - _t2,
            _t4 - _t3,
            _t5 - _t4,
            _t6 - _t5,
            _t7 - _t6,
            _t8 - _t7,
            _t9 - _t8,
            _t10 - _t9,
            _t11 - _t10,
            _t12 - _t11,
            _t13 - _t12,
            _proj,
        )
        _strip_empty_notes(result)
        return jsonify(result)
    finally:
        if conn:
            try:
                conn.close()
            except Exception as _e:
                _log.debug("[metrics] 共享连接关闭: %s", _e, exc_info=True)


# ══════════════════════════════════════════════════════════════
# 测试方法论清单 API（GET /api/test-methods）
# 双口径(case生成数/call执行数) × 三桶(系统/项目/版本)聚合
# ══════════════════════════════════════════════════════════════
@metrics_page_bp.route("/api/test-methods", methods=["GET"])
def api_test_methods():
    """返回 12 种测试方法论 + 双口径三桶统计，供「测试方法论清单」页卡片渲染。

    方法论编码见 _test_method_rules.METHOD_CODES（EP/BV/CE/DT/ST/OT/EG/FD/PC/SD/SX/SI）。
    case_count=用例生成数(test_cases)；call_count=执行次数(executions JOIN test_cases)。
    """
    from .fetchers._test_methods import fetch_test_methods

    try:
        return jsonify(fetch_test_methods())
    except Exception as _e:
        _log.warning("[test-methods] 异常: %s", _e, exc_info=True)
        return jsonify({"ok": False, "error": str(_e)[:200], "methods": []})
