# -*- coding: utf-8 -*-
"""
测试报告生成器 (ReportGenerator)
=================================
从 Validation_Intent-*.xlsx 和 requirements_breakdown.xlsx 读取数据，
生成 Markdown 格式的 E2E 测试报告。

用法：
    from src.pipeline.steps.step5.report_generator import generate_report
    result = generate_report("sample_project")
"""
from datetime import datetime
from src.core.guarded import guard
from pathlib import Path
from typing import Any
from collections import Counter
import logging
import re

# F6: 报告关键字按区域切换（zh-CN 默认，零行为变更）
from src.core.i18n import tr

# ★ 架构债清理(2026-07-24): os/time/MYSQL_DOC_URI_REPORT 已随持久化逻辑移至 _report_persist.py
_log = logging.getLogger(__name__)


# ── 子模块 re-export（保持外部 import 兼容） ──
from src.pipeline.steps.step5._db_utils import (
    _ensure_run_summary_table,
    _current_run_id,
    _load_verification_status_from_db,
    _load_execution_confidence_from_db,
    _read_report_intents,
    _read_report_requirements,
    _load_source_quotes_for_cases,
    _verify_report_stats_against_db,
)
from src.pipeline.steps.step5._confidence import (
    _collect_confidence_summary,
    _generate_smart_insights,
)
from src.pipeline.steps.step5._html import (
    _generate_html_report,
    save_html_report,
)
from src.pipeline.steps.step5._svg import (
    _svg_line_chart,
    _svg_bar_chart,
)

# ★ 架构债清理(2026-07-24): 报告持久化逻辑拆分到 _report_persist.py（§4.3 单文件≤800行）
from src.pipeline.steps.step5._report_persist import build_empty_report, persist_report


def generate_report(
    project_name: str = "sample_project",
    confidence_results: list[dict[str, Any]] = None,
    generate_html: bool = False,
    run_id: str = None,
) -> dict[str, Any]:
    """生成 Markdown 测试报告；可选地附加智能洞察并导出 HTML。"""
    from src.core.excel_handler import excel_handler
    from src.core.config_loader import config as _cfg

    intents, _case_source = _read_report_intents(excel_handler)
    reqs, _req_source = _read_report_requirements(excel_handler)
    if not intents:
        # ★ 架构债清理(2026-07-24): 空报告构建拆分到 _report_persist.py（§4.3 单文件≤800行）
        return build_empty_report(project_name)
    with guard("generate_report: from src.pipeline.utils.consistency_", log=_log, level=logging.INFO):
        from src.pipeline.utils.consistency_checker import run_and_print_checks

        run_and_print_checks("Step5前")
    ver = _cfg.project_version
    display_name = project_name
    if ver:
        display_name = "%s--%s" % (project_name, ver)
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    run_id = run_id or _current_run_id("report")
    verification_status_map = _load_verification_status_from_db(intents, project_name)

    def _get_status(c):
        return verification_status_map.get(c.get("case_id", ""), "")

    def _is_executed(c):
        return _get_status(c) in ("PASS", "FAIL", "SKIP")

    # ★ W3 T02 (2026-08-05): BLOCKED 从 TPR 分母剔除 —— 阻断用例既不算执行成功，
    #   也不算 pending，单列 blocked。默认 ATTRIBUTION_STRICT_TPR=False 时仅展示，
    #   开启后通过率分母剔除 blocked/pre_gate_blocked。
    def _is_blocked(c):
        return _get_status(c) in ("BLOCKED", "PRE_GATE_BLOCKED")

    total = len(intents)
    passed = sum(1 for c in intents if _get_status(c) == "PASS")
    failed = sum(1 for c in intents if _get_status(c) == "FAIL")
    flaky = sum(1 for c in intents if _get_status(c) == "FLAKY")  # ★ P1-4
    skipped = sum(1 for c in intents if _get_status(c) == "SKIP")
    blocked = sum(1 for c in intents if _is_blocked(c))
    pending = sum(1 for c in intents if not _is_executed(c) and not _is_blocked(c))
    # 分母口径：ATTRIBUTION_STRICT_TPR 开启 → 剔除 blocked；默认保留原口径
    try:
        from src.core.pipeline_config import _read_config_value as _cfg

        _strict_tpr = bool(_cfg("ATTRIBUTION_STRICT_TPR", False))
    except Exception:
        _strict_tpr = False
    _tpr_base = total - pending
    if _strict_tpr:
        _tpr_base = max(_tpr_base - blocked, 0)
    executed_count = _tpr_base
    if executed_count > 0:
        pass_rate = "%d/%d (%.1f%%)" % (
            passed,
            executed_count,
            (passed / executed_count * 100),
        )
    else:
        pass_rate = "未执行"
    pass_rate_value = (passed / executed_count) if executed_count > 0 else 0.0
    # ★ v5.0: 从 executions 表注入 confidence 到 intents，修复 avg_confidence=0.0
    #   根因: intents 从 test_cases 表读取，无 _confidence 字段；_collect_confidence_summary 依赖此字段。
    #   通用性: 适用于所有项目，不依赖业务逻辑。
    _exec_conf_map = _load_execution_confidence_from_db(intents, project_name)
    if _exec_conf_map:
        for _intent in intents:
            _cid = str(_intent.get("case_id", "") or "")
            if _cid in _exec_conf_map:
                _intent["_confidence"] = {"confidence": _exec_conf_map[_cid]}
    confidence_summary = _collect_confidence_summary(
        intents, confidence_results, reqs
    )  # ★ H1+H2: 传递需求数据
    catalog_stats = {}
    for intent in intents:
        cat = intent.get("catalog_name", "") or "未分类"
        if cat not in catalog_stats:
            catalog_stats[cat] = {"total": 0, "pass": 0, "fail": 0, "pending": 0}
        catalog_stats[cat]["total"] += 1
        raw_status = _get_status(intent)
        if raw_status == "PASS":
            catalog_stats[cat]["pass"] += 1
        elif raw_status == "FAIL":
            catalog_stats[cat]["fail"] += 1
        else:
            catalog_stats[cat]["pending"] += 1
    lines = [
        tr("report.title"),
        "",
        "**项目**: %s" % display_name,
        "**生成时间**: %s" % now.strftime("%Y-%m-%d %H:%M:%S"),
        "**数据来源**: Playwright 浏览器自动化执行",
        "",
        "---",
        "",
        tr("report.overview"),
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        "| 测试用例总数 | %d |" % total,
        "| 已验证 | %d |" % (total - pending),
        "| 通过 | %d |" % passed,
        "| 失败 | %d |" % failed,
        "| 跳过 | %d |" % skipped,
        "| 阻断 | %d |" % blocked,
        "| 待验证 | %d |" % pending,
        "| 达成率 | %s |" % pass_rate,
        "",
        tr("report.catalog"),
        "",
        "| 目录名称 | 总数 | 通过 | 失败 | 待验证 |",
        "|------|------|------|------|--------|",
    ]
    # ★ S45-012: 执行概览 SVG 条形图（插在执行概览表后、目录分布前）
    if total > 0:
        _overview_svg = _svg_bar_chart(
            "执行结果分布",
            [
                {"label": "通过", "value": passed, "max_val": total},
                {"label": "失败", "value": failed, "max_val": total},
                {"label": "跳过", "value": skipped, "max_val": total},
                {"label": "待验证", "value": pending, "max_val": total},
            ],
        )
        if _overview_svg:
            _idx_ov = next(
                (i for i, _l in enumerate(lines) if _l == "## 目录分布"), len(lines)
            )
            lines[_idx_ov:_idx_ov] = ["", _overview_svg, ""]
    for cat, stats in sorted(catalog_stats.items()):
        lines.append(
            "| %s | %d | %d | %d | %d |"
            % (
                cat,
                stats["total"],
                stats["pass"],
                stats["fail"],
                stats["pending"],
            )
        )
    # ★ S45-012: 目录分布 SVG 条形图（各目录通过率）
    if catalog_stats:
        _catalog_items = []
        for _cat, _stats in sorted(catalog_stats.items()):
            _rate = (
                (_stats["pass"] / _stats["total"] * 100) if _stats["total"] > 0 else 0
            )
            _catalog_items.append({"label": _cat, "value": _rate, "max_val": 100})
        _catalog_svg = _svg_bar_chart("目录通过率(%)", _catalog_items)
        if _catalog_svg:
            lines += ["", _catalog_svg, ""]
    lines += [
        "",
        "## 意图执行验证明细",
        "",
        "| 目录层级 | 目录名称 | 意图ID | 意图标题 | 意图类型 | 风险等级 | 验证结果 | 执行时间 |",
        "|----------|----------|--------|----------|----------|----------|-------------|----------|",
    ]
    failed_intents = []
    for intent in intents:
        cid = intent.get("case_id", "-")
        clevel = intent.get("catalog_level", "")
        cname = intent.get("catalog_name", "-")
        title = (intent.get("title") or "")[:30]
        ctype = intent.get("case_type", "")
        crisk_level = intent.get("risk_level", "")
        verification_time = intent.get("verification_time", "")
        status_raw = _get_status(intent)
        if not status_raw:
            display = "待验证"
        elif status_raw == "PASS":
            display = "PASS"
        elif status_raw == "FAIL":
            display = "FAIL"
            failed_intents.append(intent)
        elif status_raw == "SKIP":
            display = "SKIP"
        else:
            display = "待验证"
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s | %s |"
            % (
                clevel,
                cname,
                cid,
                title,
                ctype,
                crisk_level,
                display,
                verification_time,
            )
        )
    case_rows = []
    for intent in intents:
        payload = (
            intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
        )
        verdict = payload.get("last_intent_verdict") or {}
        what = intent.get("title") or intent.get("title", "")
        if what or verdict:
            ev_items = verdict.get("evidence_items") or []
            matched = sum(
                1 for e in ev_items if isinstance(e, dict) and e.get("matched")
            )
            case_rows.append(
                {
                    "case_id": intent.get("case_id", "-"),
                    "intent": what[:80],
                    "capability": intent.get("type", ""),
                    "verdict": verdict.get("status")
                    or ("PASS" if _get_status(intent) == "PASS" else "待验证"),
                    "evidence": "%d/%d" % (matched, len(ev_items)) if ev_items else "-",
                }
            )
    if case_rows:
        lines += [
            "",
            "## 意图与证据裁决",
            "",
            "| 意图ID | 业务意图 | 角色 | 裁决 | 证据命中 |",
            "|--------|----------|------|------|----------|",
        ]
        for r in case_rows:
            lines.append(
                "| %s | %s | %s | %s | %s |"
                % (
                    r["case_id"],
                    r["intent"],
                    r["capability"],
                    r["verdict"],
                    r["evidence"],
                )
            )

    # ★ B-6-2: Check 粒度验证明细
    check_detail_rows = []
    for intent in intents:
        payload = (
            intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
        )
        step_results = payload.get("step_results") or []
        if not step_results:
            continue
        for si, sr in enumerate(step_results):
            if not isinstance(sr, dict):
                continue
            sr_passed = sr.get("passed", False)
            sr_desc = str(sr.get("description", ""))[:50]
            sr_error = str(sr.get("error", ""))[:80]
            # 提取 checks 相关信息
            for track_key in ("ui_checks", "api_checks", "db_checks"):
                checks = (
                    sr.get(track_key) if isinstance(sr.get(track_key), list) else []
                )
                for ci, chk in enumerate(checks):
                    if not isinstance(chk, dict):
                        chk = {"raw": str(chk)}
                    chk_id = str(chk.get("check_id", ""))[:20]
                    chk_check = str(chk.get("check", ""))[:60]
                    if chk_id:
                        chk_check = f"[{chk_id}] {chk_check}"
                    chk_expect = str(chk.get("expect", ""))[:40]
                    chk_precision = str(chk.get("precision", ""))
                    chk_actual = str(chk.get("actual", sr_desc))[:40]
                    check_detail_rows.append(
                        {
                            "case_id": intent.get("case_id", "-"),
                            "step_idx": si,
                            "check_idx": ci,
                            "track": track_key.replace("_checks", "").upper(),
                            "description": sr_desc,
                            "precision": chk_precision,
                            "check": chk_check,
                            "expect": chk_expect,
                            "actual": chk_actual,
                            "passed": sr_passed,
                            "error": sr_error,
                        }
                    )
    if check_detail_rows:
        lines += [
            "",
            "## Check 粒度验证明细",
            "",
            "| 意图ID | 步骤# | Check# | 轨道 | 步骤描述 | 精度 | 校验内容 | 期望 | 实际 | 结果 |",
            "|--------|-------|--------|------|----------|------|----------|------|------|------|",
        ]
        for r in check_detail_rows:
            lines.append(
                "| %s | %d | %d | %s | %s | %s | %s | %s | %s | %s |"
                % (
                    r["case_id"],
                    r["step_idx"],
                    r["check_idx"],
                    r["track"],
                    r["description"],
                    r["precision"],
                    r["check"],
                    r["expect"],
                    r["actual"],
                    "PASS" if r["passed"] else "FAIL",
                )
            )

    with guard("generate_report: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection
        from src.core.config_loader import config as _cfg

        op_stats = {}
        try:
            from src.pipeline.steps.step4.op_cache import op_cache

            if hasattr(op_cache, "get_stats"):
                op_stats = op_cache.get_stats(_cfg.project_name) or {}
        except Exception as _e:
            _log.debug("[report_generator] 非关键异常: %s", _e, exc_info=True)
            op_stats = {}
        conn = get_connection()
        try:
            cap_summary = (
                conn.execute(
                    """
                SELECT
                    COUNT(*) AS total_count,
                    SUM(CASE WHEN is_valid=1 THEN 1 ELSE 0 END) AS valid_count,
                    SUM(CASE WHEN is_valid=0 THEN 1 ELSE 0 END) AS invalid_count,
                    COALESCE(SUM(success_count),0) AS success_total,
                    COALESCE(SUM(failure_count),0) AS failure_total,
                    COALESCE(SUM(hit_count),0) AS hit_total
                FROM capability_cache
                WHERE project=%s
                """,
                    (_cfg.project_name,),
                ).fetchone()
                or {}
            )
            event_rows = conn.execute(
                """
                SELECT event_type, COUNT(*) AS cnt
                FROM capability_cache_events
                WHERE project=%s AND created_at >= DATE_SUB(NOW(), INTERVAL 3 DAY)
                GROUP BY event_type
                """,
                (_cfg.project_name,),
            ).fetchall()
            event_counts = {
                str(r.get("event_type", "")): int(r.get("cnt", 0) or 0)
                for r in (event_rows or [])
            }
            policy = (
                conn.execute(
                    """
                SELECT max_capabilities, event_retention_days
                FROM capability_cache_policy
                WHERE project=%s
                """,
                    (_cfg.project_name,),
                ).fetchone()
                or {"max_capabilities": 1000, "event_retention_days": 3}
            )
            total_count = int(cap_summary.get("total_count", 0) or 0)
            valid_count = int(cap_summary.get("valid_count", 0) or 0)
            invalid_count = int(cap_summary.get("invalid_count", 0) or 0)
            hit_total = int(cap_summary.get("hit_total", 0) or 0)
            success_total = int(cap_summary.get("success_total", 0) or 0)
            failure_total = int(cap_summary.get("failure_total", 0) or 0)
            event_hit = event_counts.get("HIT", 0)
            event_invalid = event_counts.get("INVALIDATE", 0)
            event_pass = event_counts.get("PASS", 0)
            event_fail = event_counts.get("FAIL", 0)
            total_events_3d = sum(event_counts.values())
            cap_hit_rate = (
                (event_hit / total_events_3d * 100) if total_events_3d else 0.0
            )
            op_hit_rate = float(op_stats.get("hit_rate", 0.0) or 0.0)
            max_cap = int(policy.get("max_capabilities", 1000) or 1000)
            retention_days = int(policy.get("event_retention_days", 3) or 3)
            capacity_rate = total_count / max(max_cap, 1) * 100
            lines += [
                "",
                "## 能力库治理概览",
                "",
                "| 指标 | 数值 | 说明 |",
                "|------|------|------|",
                "| 能力库归属 | 项目级 | 按 project 隔离，不跨项目复用 |",
                "| 能力条目数 | %d/%d (%.1f%%) | 超出上限后按有效性、成功数、命中数、更新时间淘汰 |"
                % (total_count, max_cap, capacity_rate),
                "| 有效/失效能力 | %d / %d | 失效能力不会继续复用 |"
                % (valid_count, invalid_count),
                "| 成功/失败沉淀 | %d / %d | 反映能力沉淀质量 |"
                % (success_total, failure_total),
                "| 历史命中总数 | %d | capability_cache.hit_count 汇总 |" % hit_total,
                "| 事件保留策略 | 最近 %d 天 | capability_cache_events 按项目清理 |"
                % retention_days,
                "| 近3天事件量 | %d | PASS=%d, HIT=%d, INVALIDATE=%d, FAIL=%d |"
                % (total_events_3d, event_pass, event_hit, event_invalid, event_fail),
                "| 近3天能力命中率 | %.1f%% | HIT / 近3天能力事件量 |" % cap_hit_rate,
                "| op_cache 会话命中率 | %.1f%% | 操作指令缓存命中率 |" % op_hit_rate,
            ]
            cap_rows = conn.execute(
                """
                SELECT capability_name, success_count, failure_count, hit_count,
                       last_status, is_valid, invalidation_reason, updated_at
                FROM capability_cache
                WHERE project=%s
                ORDER BY updated_at DESC LIMIT 20
                """,
                (_cfg.project_name,),
            ).fetchall()
            if cap_rows:
                lines += [
                    "",
                    "## 能力库复用情况",
                    "",
                    "| 能力 | 成功次数 | 失败次数 | 命中次数 | 有效 | 最近状态 | 失效原因 |",
                    "|------|----------|----------|----------|------|----------|----------|",
                ]
                for r in cap_rows:
                    lines.append(
                        "| %s | %s | %s | %s | %s | %s | %s |"
                        % (
                            r.get("capability_name", ""),
                            r.get("success_count", 0),
                            r.get("failure_count", 0),
                            r.get("hit_count", 0),
                            "是" if int(r.get("is_valid", 1) or 0) == 1 else "否",
                            r.get("last_status", ""),
                            (r.get("invalidation_reason", "") or "-")[:40],
                        )
                    )
        finally:
            try:
                conn.close()
            except (OSError, ConnectionError) as _close_err:
                _log.warning("[Step5] conn.close 失败: %s", _close_err)
    if failed_intents:
        lines += [
            "",
            tr("report.failed"),
            "",
            "> 以下测试用例未达成。",
            "",
        ]
        # ★ S45-015: 批量查询失败用例的原文追溯 source_quote
        _failed_case_ids = [
            str(fi.get("case_id", "")) for fi in failed_intents if fi.get("case_id")
        ]
        _source_quote_map = _load_source_quotes_for_cases(
            project_name, _failed_case_ids
        )
        for intent in failed_intents:
            cid = intent.get("case_id", "-")
            cname = intent.get("catalog_name", "-")
            title = intent.get("title", "")
            intent_text = intent.get("intent_summary", "") or intent.get(
                "case_actions_json", ""
            )
            expected = intent.get("expected_outcome", "")
            precondition = intent.get("precondition", "")
            lines.extend(
                ["### %s — %s" % (cid, title[:60]), "", "- **目录名称**: %s" % cname]
            )
            if precondition:
                lines.append("- **预置条件**: %s" % str(precondition)[:200])
            if intent_text:
                case_preview = str(intent_text)[:200].replace("\n", " ")
                lines.append("- **测试用例**: %s" % case_preview)
            if expected:
                lines.append("- **预期结果**: %s" % str(expected)[:200])
            # ★ S45-015: 追加原始需求段落 source_quote
            _sq = _source_quote_map.get(str(cid), "")
            if _sq:
                lines.append("- **需求原文**: > %s" % str(_sq)[:300].replace("\n", " "))
            # ★ B-6-4: 失败层级推断
            payload = (
                intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
            )
            step_results = payload.get("step_results") or []
            failed_layers = set()
            failed_step_descs = []
            for sr in step_results:
                if not isinstance(sr, dict) or sr.get("passed", False):
                    continue
                sr_desc = str(sr.get("description", ""))[:60]
                sr_error = str(sr.get("error", ""))[:100]
                failed_step_descs.append("%s (%s)" % (sr_desc, sr_error))
                if sr.get("ui_checks"):
                    failed_layers.add("UI")
                if sr.get("api_checks"):
                    failed_layers.add("API")
                if sr.get("db_checks"):
                    failed_layers.add("DB")
                if not failed_layers:
                    # 硬验证失败（无checks但有expected）
                    failed_layers.add("HardVerify")
            if failed_layers:
                lines.append("- **推断失败层**: %s" % " / ".join(sorted(failed_layers)))
            if failed_step_descs:
                lines.append("- **失败步骤**: %s" % "; ".join(failed_step_descs[:3]))
            lines.append("")
    # ★ D (2026-07-10): 人工补测建议清单（覆盖点清单）
    #   交叉比对 requirements + test_cases + executions，输出未覆盖/失败/Flaky 的人工补测建议
    #   契约: 让人类只补测引擎漏掉的部分，而非全部重跑
    with guard("generate_report: from ._manual_retest import generate", log=_log, level=logging.WARNING):
        from ._manual_retest import generate_manual_retest_section

        _mr = generate_manual_retest_section(
            project_name, run_id, intents, reqs, failed_intents
        )
        if _mr.get("success") and _mr.get("markdown"):
            lines.append(_mr["markdown"])
    # ★ B-6-5: 跨轨一致性报告
    consistency_rows = []
    for intent in intents:
        payload = (
            intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
        )
        step_results = payload.get("step_results") or []
        for sr in step_results:
            if not isinstance(sr, dict):
                continue
            consistency = sr.get("consistency")
            if not isinstance(consistency, dict):
                continue
            cc_field = sr.get("description", "")
            consistency_rows.append(
                {
                    "case_id": intent.get("case_id", "-"),
                    "step": cc_field[:60],
                    "passed": consistency.get("passed", False),
                    "inconclusive": consistency.get("inconclusive", False),
                    "evidence": str(consistency.get("evidence", ""))[:120],
                    "values": consistency.get("values", {}),
                }
            )
    if consistency_rows:
        lines += [
            "",
            "## 跨轨一致性报告",
            "",
            "> 前端/UI、接口/API、数据库/DB 三值一致性比对结果。",
            "",
            "| 意图ID | 步骤 | UI值 | API值 | DB值 | 结果 | 证据 |",
            "|--------|------|------|-------|------|------|------|",
        ]
        for r in consistency_rows:
            vals = r["values"]
            ui_v = str(vals.get("ui", "-"))
            api_v = str(vals.get("api", "-"))
            db_v = str(vals.get("db", "-"))
            if r["inconclusive"]:
                status = "INCONCLUSIVE"
            elif r["passed"]:
                status = "PASS"
            else:
                status = "FAIL"
            lines.append(
                "| %s | %s | %s | %s | %s | %s | %s |"
                % (r["case_id"], r["step"], ui_v, api_v, db_v, status, r["evidence"])
            )
    with guard("generate_report: from src.extensions.coverage_tracker", log=_log, level=logging.WARNING):
        from src.extensions.coverage_tracker import coverage_tracker
        from src.pipeline.utils.coverage_store import coverage_store

        db_cov = coverage_store.load_coverage()
        if db_cov and db_cov.get("matrix"):
            cov_result = db_cov
            _log.info(
                "  [报告] 覆盖矩阵: 取自 MySQL coverage_results (%d 条需求)"
                % len(db_cov.get("matrix", {}))
            )
        else:
            cov_result = coverage_tracker.compute_coverage(reqs, intents)
            coverage_store.upsert_coverage(cov_result)
            _log.info("  [报告] 覆盖矩阵: 重新计算并写入 MySQL coverage_results")
        cov_section = coverage_tracker.format_report_section(cov_result)
        lines.append(cov_section)
    with guard("generate_report: from src.extensions.traceability_mod", log=_log, level=logging.WARNING):
        from src.extensions.traceability_model import traceability_engine

        cached_links = traceability_engine._load_cache()
        if cached_links:
            cov_report = traceability_engine.compute_all_coverage(cached_links, intents)
            trace_section = traceability_engine.format_report_section(cov_report)
            lines.append(trace_section)
    # ★ 追溯校验汇总（trace_validator）：检测测试用例中是否存在幻觉表名/API路径
    with guard("generate_report: from src.extensions.trace_validator ", log=_log, level=logging.WARNING):
        from src.extensions.trace_validator import validate_checks_against_trace
        from src.extensions.traceability_model import traceability_engine

        trace_cache = traceability_engine._load_cache()
        if trace_cache and intents:
            validation_result = validate_checks_against_trace(
                intents, trace_links=trace_cache, db_schema={}
            )
            if validation_result.get("intents"):
                # why: 追溯校验的"通过/风险"计数与执行统计是两种语义，复用 passed/failed
                #   会把 run_summary 的通过/失败数覆盖成防幻觉校验结果。
                _trace_ok = sum(
                    1
                    for r in validation_result["intents"]
                    if r["result"].get("valid", True)
                )
                _trace_bad = len(validation_result["intents"]) - _trace_ok
                lines.append(
                    "\n## 追溯校验（防幻觉）\n\n"
                    "| 指标 | 值 |\n"
                    "|:---|:---|\n"
                    "| 校验用例数 | %d |\n"
                    "| 通过 | %d |\n"
                    "| 存在幻觉风险 | %d |\n"
                    % (len(validation_result["intents"]), _trace_ok, _trace_bad)
                )
                if _trace_bad > 0:
                    lines.append("\n**幻觉风险详情**（表名/API路径不在追溯链路中）：\n")
                    for item in validation_result["intents"]:
                        r = item["result"]
                        if not r.get("valid", True):
                            issues = r.get("issues", [])
                            warnings = r.get("warnings", [])
                            lines.append(
                                "- **%s** (%s)"
                                % (item["case_id"], item.get("title", ""))
                            )
                            for issue in issues:
                                lines.append("  - ❌ %s" % issue)
                            for warn in warnings:
                                lines.append("  - ⚠️ %s" % warn)
    with guard("generate_report: from src.extensions.diff_engine impo", log=_log, level=logging.WARNING):
        from src.extensions.diff_engine import diff_engine
        from src.pipeline.utils.diff_snapshot_store import diff_snapshot_store

        old_versions = diff_snapshot_store.list_versions()
        prev_versions = old_versions[1:] if len(old_versions) > 1 else []
        old_reqs = None
        old_api = None
        old_db_text = ""
        old_intents_snap = None
        if prev_versions:
            prev_ver = prev_versions[0]
            old_reqs = diff_snapshot_store.load_snapshot(
                "requirements", version_label=prev_ver
            )
            old_api = diff_snapshot_store.load_snapshot(
                "api_schema", version_label=prev_ver
            )
            old_db_data = diff_snapshot_store.load_snapshot(
                "db_schema", version_label=prev_ver
            )
            if isinstance(old_db_data, dict):
                old_db_text = old_db_data.get("schema_text", "")
            old_intents_snap = diff_snapshot_store.load_snapshot(
                "test_cases", version_label=prev_ver
            )
        has_any_diff = False
        if old_reqs is not None:
            all_reqs = reqs
            req_diff = diff_engine.diff_requirements(all_reqs, old_reqs)
            if req_diff.has_changes:
                has_any_diff = True
                lines.append(diff_engine.format_diff_report(req_diff))
                try:
                    from src.extensions.traceability_model import traceability_engine
                    from src.extensions.diff_engine import impact_analyzer

                    trace_links = traceability_engine._load_cache() or {}
                    impact = impact_analyzer.analyze(req_diff, trace_links, intents)
                    lines.append(impact_analyzer.format_impact_report(impact))
                except (ValueError, TypeError, KeyError) as _diff_err:
                    _log.warning("[Step5] API schema diff 失败: %s", _diff_err)
        if old_api is not None:
            try:
                new_api = diff_snapshot_store.load_snapshot("api_schema")
                if new_api is not None:
                    api_diff = diff_engine.diff_api_schema(new_api, old_api)
                    if api_diff.has_changes:
                        has_any_diff = True
                        lines.append(diff_engine.format_diff_report(api_diff))
            except (ValueError, TypeError, KeyError) as _diff_err:
                _log.warning("[Step5] API schema diff(旧) 失败: %s", _diff_err)
        if old_db_text:
            try:
                from src.core.session_context import session_ctx

                new_db_text = session_ctx.for_traceability()
                if new_db_text and old_db_text:
                    db_diff = diff_engine.diff_db_schema(new_db_text, old_db_text)
                    if db_diff.has_changes:
                        has_any_diff = True
                        lines.append(diff_engine.format_diff_report(db_diff))
            except (ValueError, TypeError, KeyError) as _diff_err:
                _log.warning("[Step5] DB schema diff 失败: %s", _diff_err)
        if old_intents_snap is not None and isinstance(old_intents_snap, list):
            new_intent_snap = [
                {
                    "case_id": c.get("case_id"),
                    "title": c.get("title"),
                    "case_type": c.get("case_type"),
                    "risk_level": c.get("risk_level"),
                    "verification_status": c.get("verification_status", ""),
                }
                for c in intents
                if c.get("case_id")
            ]
            from src.pipeline.helpers import _diff_intent_snapshots

            case_diff = _diff_intent_snapshots(old_intents_snap, new_intent_snap)
            if case_diff["has_changes"]:
                has_any_diff = True
                lines.extend(
                    [
                        "## 测试用例版本对比",
                        "",
                        f"- **旧版本**: {len(old_intents_snap)} 条测试用例",
                        f"- **新版本**: {len(new_intent_snap)} 条测试用例",
                        f"- **新增**: {case_diff['added']} 条 | **删除**: {case_diff['removed']} 条 | **修改**: {case_diff['modified']} 条",
                        "",
                    ]
                )
        if not has_any_diff:
            lines.extend(
                ["", "## 变更对比", "", "（无历史版本或所有维度均无变更）", ""]
            )
    with guard("generate_report: from src.extensions.confidence_calcu", log=_log, level=logging.WARNING):
        from src.extensions.confidence_calculator import ConfidenceCalculator
        from src.pipeline.utils.case_store import case_store

        all_intents = case_store.list_intents()
        conf_high = conf_med = conf_low = conf_none = 0
        total_structured = 0
        total_with_struct = 0
        for c in all_intents:
            cf = c.get("_confidence", {})
            cl = cf.get("level", "") if isinstance(cf, dict) else ""
            if cl == "high":
                conf_high += 1
            elif cl == "medium":
                conf_med += 1
            elif cl == "low":
                conf_low += 1
            else:
                conf_none += 1
            sr = c.get("_structured_rate")
            if sr is not None:
                total_with_struct += 1
                total_structured += sr
        avg_struct_rate = (
            (total_structured / total_with_struct) if total_with_struct > 0 else 0
        )
        try:
            from src.pipeline.steps.step4.op_cache import op_cache

            cs = op_cache.get_stats() if hasattr(op_cache, "get_stats") else {}
            cache_rate = cs.get("session_rate", 0)
        except Exception:
            cache_rate = 0
        lines += [
            "",
            "## 智能指标",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            "| 高置信度测试用例 (≥90%%) | %d |" % conf_high,
            "| 中置信度测试用例 (70-90%%) | %d |" % conf_med,
            "| 低置信度测试用例 (<70%%) | %d |" % conf_low,
            "| 未计算置信度 | %d |" % conf_none,
            "| 平均结构化率 | %.1f%% |" % (avg_struct_rate * 100),
            "| 操作缓存命中率 | %.1f%% |" % (cache_rate * 100),
            "",
        ]
        # ★ P2-3: 置信度趋势告警（跨版本下降>15%自动告警）
        try:
            total_conf_intents = conf_high + conf_med + conf_low
            if total_conf_intents > 0:
                avg_conf_score = (
                    conf_high * 0.95 + conf_med * 0.80 + conf_low * 0.40
                ) / total_conf_intents
                trend = ConfidenceCalculator.check_confidence_trend(
                    avg_conf_score, project_name
                )
                if trend.get("alert"):
                    lines += [
                        "> ⚠️ **置信度趋势告警**: %s" % trend.get("message", ""),
                        "",
                    ]
        except (ValueError, TypeError, OSError, KeyError) as _conf_err:
            _log.warning("[Step5] 置信度计算失败: %s", _conf_err)
    lines += ["", "---", "", tr("report.footer", timestamp=timestamp)]
    with guard("generate_report: insights = _generate_smart_insights(", log=_log, level=logging.WARNING):
        insights = _generate_smart_insights(
            intents, confidence_results, project_name, run_id=run_id
        )
        if insights:
            lines.append(insights)
    # ★ S45-013: 蓝红对抗报告生成 + 关键结论嵌入主报告 Markdown
    _br_result = None
    with guard("generate_report: from src.core.pipeline_config import", log=_log):
        from src.core.pipeline_config import pipeline_config

        if getattr(pipeline_config, "step5_blue_red_enabled", True):
            from ._blue_red_report import generate_blue_red_report

            br_stats = {
                "total": total,
                "passed": passed,
                "failed": failed,
                "flaky": flaky,
                "skipped": skipped,
                "pending": pending,
                "pass_rate_value": pass_rate_value,
                "failed_intents": failed_intents,
            }
            # ★ 通用修复(2026-07-26, §10.9): 蓝红报告生成加超时包装。
            #   根因: generate_blue_red_report 内部 AI 调用阻塞 → 永不返回
            #   → 外部 except Exception 无法捕获（不抛异常）。
            #   修复: 用 ThreadPoolExecutor.result(timeout=300) 包装，超时后降级跳过。
            #   通用性: 适用于所有可能阻塞的外部函数调用。
            from concurrent.futures import (
                ThreadPoolExecutor as _BRExecutor,
                TimeoutError as _BRTimeout,
            )

            with _BRExecutor(max_workers=1) as _br_exec:
                _br_fut = _br_exec.submit(
                    generate_blue_red_report, intents, br_stats, catalog_stats, lines
                )
                try:
                    _br_result = _br_fut.result(timeout=300)
                except _BRTimeout:
                    _log.warning("[蓝红 Step5] 蓝红报告生成超时(300s)，跳过增强")
                    _br_result = None
            if _br_result and _br_result.get("success"):
                _br_enh = _br_result.get("enhancements", {}) or {}
                _br_red = _br_result.get("red_team", {}) or {}
                _br_persp = _br_result.get("perspectives", {}) or {}
                lines += ["", "---", "", "## 蓝红对抗分析", ""]
                _rel = _br_enh.get("release_recommendation")
                if _rel:
                    _rel_map = {
                        "go": "✅ 建议上线",
                        "no_go": "❌ 不建议上线",
                        "cautious": "⚠️ 谨慎上线",
                    }
                    lines.append("- **发布建议**: %s" % _rel_map.get(_rel, _rel))
                _risks = _br_enh.get("risks") or []
                if _risks:
                    lines += ["", "### P0 风险项", ""]
                    for _r in _risks:
                        lines.append("- %s" % _r)
                _dec = _br_persp.get("decision", {}) or {}
                _summary = _dec.get("summary")
                if _summary:
                    lines += ["", "### 决策摘要", ""]
                    lines.append(_summary)
                _trend = _dec.get("trend_summary")
                _trend_dir = _dec.get("pass_rate_trend")
                if _trend or _trend_dir:
                    lines += ["", "### 趋势分析", ""]
                    if _trend_dir:
                        _trend_map = {
                            "up": "📈 上升",
                            "down": "📉 下降",
                            "stable": "➡️ 平稳",
                        }
                        lines.append(
                            "- 通过率趋势: %s" % _trend_map.get(_trend_dir, _trend_dir)
                        )
                    if _trend:
                        lines.append("- %s" % _trend)
                _tech = _br_persp.get("technical", {}) or {}
                _patterns = _tech.get("failure_patterns") or []
                if _patterns:
                    lines += ["", "### 失败根因模式", ""]
                    lines += [
                        "| 模式 | 出现次数 | 根因 |",
                        "|------|----------|------|",
                    ]
                    for _p in _patterns:
                        if isinstance(_p, dict):
                            lines.append(
                                "| %s | %s | %s |"
                                % (
                                    _p.get("pattern", "-"),
                                    _p.get("count", "-"),
                                    _p.get("root_cause", "-"),
                                )
                            )
                _impr = _br_enh.get("improvement_areas") or []
                if _impr:
                    lines += ["", "### 改进方向", ""]
                    for _i in _impr:
                        lines.append("- %s" % _i)
                _score = _br_red.get("consistency_score")
                _verdict = _br_red.get("verdict")
                if _score is not None or _verdict:
                    lines += ["", "### 红队一致性验证", ""]
                    if _score is not None:
                        lines.append("- 一致性分数: %.1f%%" % (float(_score) * 100))
                    if _verdict:
                        _verdict_map = {
                            "consistent": "✅ 一致",
                            "minor_issues": "⚠️ 轻微问题",
                            "inconsistent": "❌ 不一致",
                        }
                        lines.append(
                            "- 裁决: %s" % _verdict_map.get(_verdict, _verdict)
                        )
                    _issues = _br_red.get("issues") or []
                    for _iss in _issues:
                        if isinstance(_iss, dict):
                            lines.append(
                                "  - [%s] %s"
                                % (_iss.get("type", "?"), _iss.get("detail", ""))
                            )
                _log.info(
                    "[蓝红 Step5] 报告增强完成并嵌入主报告: consistency=%.2f",
                    _br_red.get("consistency_score", 0),
                )
    # ★ S45-014: 报告数据自校验（与 executions 表 COUNT 对比）
    with guard("generate_report: _verify_stats = { 'total': total, 'p", log=_log, level=logging.WARNING):
        _verify_stats = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "pending": pending,
        }
        _verification = _verify_report_stats_against_db(
            project_name, run_id, _verify_stats
        )
        if not _verification.get("skipped"):
            lines += ["", "---", "", "## 报告数据自校验", ""]
            if _verification.get("verified"):
                lines.append("✅ 报告统计数据与 executions 表记录一致。")
            else:
                lines.append("⚠️ 报告统计数据与 executions 表记录不一致：")
                for _m in _verification.get("mismatches", []):
                    lines.append("- %s" % _m)
            _db_counts = _verification.get("db_counts", {})
            if _db_counts:
                lines += [
                    "",
                    "**DB 实际聚合**: %s"
                    % ", ".join(
                        "%s=%d" % (k, v) for k, v in sorted(_db_counts.items())
                    ),
                ]
    # ★ P2-12: 引擎自评估章节（从 golden_set 对比实际 verdict）
    #   拆出到 _self_eval.py（§4.3 单文件≤800行 + 职责分离）
    #   返回 (markdown, metrics)，metrics 供 run_summary.summary_json 持久化
    from src.pipeline.steps.step5._self_eval import attach_self_eval_section

    _se_md, _se_metrics = attach_self_eval_section(project_name)
    if _se_md:
        lines.append(_se_md)
    # ★ W3 T02/T05 (2026-08-05): 归因 / 幂等 / 性能 报告区块（开关默认 OFF → 零行为变更）
    with guard("generate_report: from src.core.pipeline_config import", log=_log, level=logging.WARNING):
        from src.core.pipeline_config import _read_config_value as _cfg

        # 归因区块
        if bool(_cfg("ATTRIBUTION_ENABLED", False)):
            from src.pipeline.steps.step5.attribution import build_attribution_report
            from src.pipeline.steps.step5._report_sections import (
                render_attribution_section,
            )

            _attr = build_attribution_report(project_name, run_id or "")
            _attr_md = render_attribution_section(_attr)
            if _attr_md:
                lines += ["", "---", "", _attr_md]
        # 幂等区块
        if bool(_cfg("IDEMPOTENCY_ENABLED", False)):
            from src.pipeline.steps.step5._report_sections import (
                render_idempotency_section,
            )

            _idem_results = []
            for _cid in [c.get("case_id", "") for c in intents if c.get("case_id")][
                :20
            ]:
                try:
                    from src.pipeline.steps.step5.idempotency import compare_case_runs

                    _r = compare_case_runs(project_name, _cid, "", "")
                    if _r.get("source") not in (
                        "param_error",
                        "insufficient_runs",
                        "exception",
                    ):
                        _r["case_id"] = _cid
                        _idem_results.append(_r)
                except Exception:  # noqa: BLE001
                    continue
            _idem_md = render_idempotency_section(_idem_results)
            if _idem_md:
                lines += ["", "---", "", _idem_md]
        # 性能区块
        if bool(_cfg("PERF_BASELINE_ENABLED", False)):
            from src.pipeline.steps.step5.perf_baseline import build_perf_baseline
            from src.pipeline.steps.step5._report_sections import render_perf_section

            _perf = build_perf_baseline(project_name, run_id or "")
            _perf_md = render_perf_section(_perf)
            if _perf_md:
                lines += ["", "---", "", _perf_md]
        # ★ 性能门禁区块（PERF_GATE_ENABLED 默认 OFF → 零行为变更）
        if bool(_cfg("PERF_GATE_ENABLED", False)):
            from src.pipeline.steps.step5._perf_gate_report import (
                build_perf_gate_section,
            )

            _pg_md = build_perf_gate_section(project_name, run_id or "")
            if _pg_md:
                lines += ["", "---", "", _pg_md]
        # ★ 性能跨运行趋势区块（PERF_TREND_ENABLED 默认 OFF → 零行为变更）
        #   数据源：data/perf_baseline/perf_*.json 的历史运行 → TrendAnalyzer；
        #   横向补全跨运行退化预警（配置驱动，无历史数据时诚实不渲染）。
        if bool(_cfg("PERF_TREND_ENABLED", False)):
            from src.extensions.perf_baseline.trend import TrendAnalyzer
            from src.pipeline.steps.step5._report_sections import (
                render_perf_trend_section,
                render_perf_trend_svg,
            )

            with guard("generate_report: _pt = TrendAnalyzer().analyze_all()", log=_log, level=logging.WARNING):
                _pt = TrendAnalyzer().analyze_all()
                _pt_md = render_perf_trend_section(_pt)
                if _pt_md:
                    # 先在表格前注入折线图（单行内联 SVG，HTML 报告可见）
                    _pt_svg = render_perf_trend_svg(_pt.get("series") or {})
                    if _pt_svg:
                        lines += ["", _pt_svg]
                    lines += ["", "---", "", _pt_md]
        # ★ 可访问性（a11y）报告区块（A11Y_REPORT_ENABLED 默认 OFF → 零行为变更）
        #   数据源：oracle_results（oracle_type='accessibility'），step4 采集落库后
        #   本区块聚合渲染。与 perf 门禁区块同构，满足「报告须含 a11y/perf 维度」约定。
        if bool(_cfg("A11Y_REPORT_ENABLED", False)):
            from src.pipeline.steps.step5._a11y_report import build_a11y_section

            _a11y_md = build_a11y_section(project_name, run_id or "")
            if _a11y_md:
                lines += ["", "---", "", _a11y_md]
        # ★ 安全（security）报告区块（SECURITY_REPORT_ENABLED 默认 ON → 全量展示）
        #   数据源：oracle_results（oracle_type='security'），step4 横切门禁采集合集后
        #   本区块聚合渲染。与 a11y/perf/visual 对齐，补全「覆盖全面…安全…」的一等公民维度。
        if bool(_cfg("SECURITY_REPORT_ENABLED", True)):
            from src.pipeline.steps.step5._security_report import build_security_section

            _sec_md = build_security_section(project_name, run_id or "")
            if _sec_md:
                lines += ["", "---", "", _sec_md]
        # ★ 视觉（visual）报告区块（VISUAL_REPORT_ENABLED 默认 OFF → 零行为变更）
        #   数据源：visual_review（run 级 execution_id=0）。此前 visual 仅落库零展示，
        #   补全三横切展示（a11y/perf 已有区块归口），本块聚合最近视觉复核结果。
        if bool(_cfg("VISUAL_REPORT_ENABLED", False)):
            from src.pipeline.steps.step5._visual_report import build_visual_section

            _vis_md = build_visual_section(project_name, run_id or "")
            if _vis_md:
                lines += ["", "---", "", _vis_md]
        # ★ W4-A (2026-08-06): 假 PASS 审计区块（FAKE_PASS_AUDIT_ENABLED 默认 OFF → 零行为变更）
        if bool(_cfg("FAKE_PASS_AUDIT_ENABLED", False)):
            from src.pipeline.steps.step5.fake_pass_audit import (
                audit_fake_passes,
                summarize_fake_pass_audit,
            )

            _fp = audit_fake_passes(project_name, run_id or "")
            _fp_md = summarize_fake_pass_audit(_fp)
            if _fp_md:
                lines += ["", "---", "", _fp_md]
        # ★ W4-B (2026-08-06): 引擎健康区块（ENGINE_HEALTH_ENABLED 默认 OFF → 零行为变更）
        if bool(_cfg("ENGINE_HEALTH_ENABLED", False)):
            from src.pipeline.steps.step5.engine_health import (
                engine_health_report,
                summarize_engine_health,
            )

            _eh = engine_health_report(project_name, run_id or "")
            _eh_md = summarize_engine_health(_eh)
            if _eh_md:
                lines += ["", "---", "", _eh_md]
        # ★ 变更影响→回归范围 知识库（CHANGE_IMPACT_KB_ENABLED 默认 True → 零成本，只读历史数据）
        if bool(_cfg("CHANGE_IMPACT_KB_ENABLED", True)):
            from src.extensions.change_impact_kb.report_section import (
                build_change_impact_section,
                render_change_impact_section,
            )

            _ci = build_change_impact_section(project_name, run_id or "")
            _ci_md = render_change_impact_section(_ci)
            if _ci_md:
                lines += ["", "---", "", _ci_md]
    # ★ Bug 报告死链接线(2026-09-04)：把 build_bug_report 产出渲染为主报告"Bug 清单/缺陷"区段。
    #   此前 _bug_report/bug_ticket/minimal_repro 整链无运行时调用方。无条件渲染，
    #   但 fail-open：无 FAIL/ERROR 时如实显示"无缺陷"，数据源异常不打断主流程。
    with guard("generate_report: from src.pipeline.steps.step5._repor", log=_log, level=logging.WARNING):
        from src.pipeline.steps.step5._report_sections import (
            render_bug_report_section,
        )

        _bug_md = render_bug_report_section(project_name, run_id or "")
        if _bug_md:
            lines += ["", "---", "", _bug_md]
    from src.core.config_loader import config

    report_content = "\n".join(lines)
    # ★ 架构债清理(2026-07-24): 报告持久化拆分到 _report_persist.py（§4.3 单文件≤800行）
    try:
        from src.core.versioning import current_version_id

        _version_id = current_version_id(project_name)
    except Exception as _e:
        _log.debug("[Step5] 获取 version_id 失败: %s", _e, exc_info=True)
        _version_id = 0
    _persist_result = persist_report(
        project_name=project_name,
        run_id=run_id or "",
        timestamp=timestamp,
        report_content=report_content,
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        pending=pending,
        flaky=flaky,
        executed_count=executed_count,
        pass_rate_value=pass_rate_value,
        confidence_summary=confidence_summary,
        catalog_stats=catalog_stats,
        intents=intents,
        version_id=_version_id,
        self_eval_metrics=_se_metrics or {},
    )
    report_path = Path(_persist_result["report_path"])
    flaky_rate = "%.1f%%" % (flaky / max(total, 1) * 100)
    _log.info(
        "  测试用例总数: %d | 通过: %d | 失败: %d | Flaky: %d (%s) | 待验证: %d",
        total,
        passed,
        failed,
        int(flaky),
        flaky_rate,
        pending,
    )
    summary_stats = {
        "total": total,
        "passed": passed,
        "failed": failed,
        "flaky": flaky,
        "flaky_rate": flaky_rate,
        "skipped": skipped,
        "pending": pending,
    }
    html_path = None
    if generate_html:
        with guard("generate_report: html_doc = _generate_html_report('\n", log=_log, level=logging.WARNING):
            html_doc = _generate_html_report("\n".join(lines), summary_stats)
            html_path = save_html_report(
                html_doc,
                project_name,
                run_id=run_id or "",
                version_id=_version_id,
                version_label=str(getattr(_cfg, "project_version", "") or ""),
            )
    result = {
        "success": True,
        "report_path": str(report_path),
        "stats": summary_stats,
        "report_markdown": report_content,  # ★ S45-012: 返回报告内容供调用方/测试使用
    }
    if html_path:
        result["html_report_path"] = html_path

    # ★ S45-013: 蓝红对抗报告已在 report_content 拼接前生成并嵌入主报告 Markdown
    if _br_result and _br_result.get("success"):
        result["_blue_red"] = {
            "perspectives": _br_result.get("perspectives", {}),
            "red_team": _br_result.get("red_team", {}),
            "enhancements": _br_result.get("enhancements", {}),
        }

    return result
