# -*- coding: utf-8 -*-
"""
智能洞察报告
"""
from typing import Any
from src.core.guarded import guard
from collections import Counter
import logging

_log = logging.getLogger(__name__)

from ._svg_charts import _generate_learning_curve_svg, _generate_cache_dashboard_svg


def _generate_smart_insights(
    all_intents: list[dict[str, Any]],
    confidence_results: list[dict[str, Any]] = None,
    project_name: str = None,
    run_id: str = None,
) -> str:
    """
    基于测试用例资产 + 置信度结果，生成可行动的洞察报告（Markdown 文本）。
    可选传入 project_name 以加载学习曲线 / 缓存仪表盘作为智能仪表盘追加到末尾。
    """
    lines = ["", "## 智能洞察", ""]
    conf_map = {}
    if confidence_results:
        for item in confidence_results:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("case_id", ""))
            if cid:
                conf_map[cid] = item
    intents_with_conf = []
    for c in all_intents:
        cid = str(c.get("case_id", ""))
        if cid in conf_map:
            intents_with_conf.append((c, conf_map[cid]))
            continue
        inner = c.get("_confidence")
        if isinstance(inner, dict) and inner.get("confidence") is not None:
            intents_with_conf.append((c, inner))
    lines.extend(["### 1. 低置信度测试用例（建议人工复核）", ""])
    low_conf = sorted(
        [
            (c, ci)
            for c, ci in intents_with_conf
            if float(ci.get("confidence", 1.0)) < 0.5
        ],
        key=lambda t: float(t[1].get("confidence", 1.0)),
    )[:10]
    if low_conf:
        lines.extend(
            ["| 意图ID | 标题 | 置信度 | 等级 |", "|--------|------|--------|------|"]
        )
        for c, ci in low_conf:
            title = (c.get("title", "") or "")[:40]
            conf_val = float(ci.get("confidence", 0.0))
            level = ci.get("level", "low")
            lines.append(
                "| %s | %s | %.3f | %s |"
                % (c.get("case_id", "-"), title, conf_val, level)
            )
    else:
        lines.append("- 暂无置信度低于 0.5 的测试用例。")
    lines.extend(["", "### 2. 结构化率偏低的模块（建议补全需求文档）", ""])
    mod_struct = {}  # catalog_name -> [sr_values]
    for c in all_intents:
        cat = c.get("catalog_name") or "未分类"
        sr = c.get("_structured_rate")
        if sr is not None:
            mod_struct.setdefault(cat, []).append(float(sr))
    low_modules = []
    for cat, vals in mod_struct.items():
        avg = sum(vals) / len(vals)
        if avg < 0.5:
            low_modules.append((cat, avg, len(vals)))
    low_modules.sort(key=lambda t: t[1])
    if low_modules:
        lines.extend(
            ["| 模块 | 平均结构化率 | 意图数 |", "|------|--------------|--------|"]
        )
        for cat, avg, cnt in low_modules:
            lines.append("| %s | %.1f%% | %d |" % (cat, avg * 100, cnt))
    else:
        lines.append("- 所有模块平均结构化率均 ≥ 50%，整体质量良好。")
    lines.extend(["", "### 3. 最慢的 5 个测试用例（潜在性能问题）", ""])
    durations = []
    for c in all_intents:
        raw = c.get("verification_time") or c.get("duration") or c.get("elapsed")
        if raw in (None, ""):
            continue
        try:
            d = float(raw)
        except (TypeError, ValueError):
            continue
        if d > 0:
            durations.append((c, d))
    durations.sort(key=lambda t: t[1], reverse=True)
    top5 = durations[:5]
    if top5:
        lines.extend(["| 意图ID | 标题 | 耗时(秒) |", "|--------|------|----------|"])
        for c, d in top5:
            title = (c.get("title", "") or "")[:40]
            lines.append("| %s | %s | %.2f |" % (c.get("case_id", "-"), title, d))
    else:
        lines.append("- 暂无耗时数据，可在执行后补充 verification_time 字段。")
    lines.extend(["", "### 4. 断言通过率最低的模块", ""])
    mod_stats = {}
    for c in all_intents:
        cat = c.get("catalog_name") or "未分类"
        s = mod_stats.setdefault(cat, {"total": 0, "pass": 0, "fail": 0})
        s["total"] += 1
        status = (c.get("verification_status") or "").upper()
        if status == "PASS":
            s["pass"] += 1
        elif status == "FAIL":
            s["fail"] += 1
    module_rates = []
    for cat, s in mod_stats.items():
        executed = s["pass"] + s["fail"]
        rate = (s["pass"] / executed) if executed > 0 else None
        if rate is not None:
            module_rates.append((cat, rate, executed, s["fail"]))
    module_rates.sort(key=lambda t: t[1])
    worst = module_rates[:5]
    if worst:
        lines.extend(
            ["| 模块 | 通过率 | 已执行 | 失败 |", "|------|--------|--------|------|"]
        )
        for cat, rate, executed, fails in worst:
            lines.append(
                "| %s | %.1f%% | %d | %d |" % (cat, rate * 100, executed, fails)
            )
    else:
        lines.append("- 暂无已测试用例的断言数据。")
    lines.extend(["", "### 5. 总体置信度分布", ""])
    levels = []
    for _, ci in intents_with_conf:
        levels.append(ci.get("level", "unknown"))
    total_conf = len(levels) or 1
    counter = Counter(levels)
    lines.extend(["| 等级 | 数量 | 占比 |", "|------|------|------|"])
    for lv in ("high", "medium", "low", "unknown"):
        cnt = counter.get(lv, 0)
        if cnt == 0 and lv == "unknown":
            continue
        lines.append("| %s | %d | %.1f%% |" % (lv, cnt, cnt / total_conf * 100))

    # ★ P2-3: 置信度因子 breakdown（让QA知道"到底哪里不可信"）
    lines.extend(["", "### 5.1 置信度因子详细 Breakdown", ""])
    lines.extend(
        ["| 因子 | 权重 | 当前均值 | 说明 |", "|------|------|----------|------|"]
    )
    factor_labels = {
        "assertion_rate": ("断言通过率", "断言全部通过的比例"),
        "structured_rate": ("结构化率", "意图动作含intent/actions/checks的比例"),
        "hard_verify_rate": ("硬验证率", "DOM/API客观验证通过的比例"),
        "first_try_rate": ("首次成功率", "AI首轮即成功的意图动作比例"),
        "semantic_align": ("语义对齐度", "需求-测试用例向量相似度"),
        "cache_hit_rate": (
            "经验复用命中率",
            "op_cache 与 capability_cache 的合成命中比例",
        ),
        "op_cache_hit_rate": ("操作缓存命中率", "页面操作指令缓存命中比例，不单独加权"),
        "capability_hit_rate": ("能力库命中率", "业务能力缓存命中比例，不单独加权"),
    }
    try:
        from src.extensions.confidence_calculator import ConfidenceCalculator

        weights = dict(ConfidenceCalculator.WEIGHTS)
    except Exception as _e:
        _log.debug("[_insights] 非关键异常: %s", _e, exc_info=True)
        weights = {}
    # 聚合所有测试用例的因子均值
    factor_sums = {k: 0.0 for k in factor_labels}
    factor_count = 0
    for _, ci in intents_with_conf:
        factors = ci.get("factors", {})
        if factors:
            for k in factor_sums:
                factor_sums[k] += factors.get(k, 0)
            factor_count += 1
    if factor_count > 0:
        for key, (label, desc) in factor_labels.items():
            avg = factor_sums[key] / factor_count
            weight = weights.get(key, 0)
            lines.append(
                "| %s (%s) | %.0f%% | %.1f%% | %s |"
                % (label, key, weight * 100, avg * 100, desc)
            )
    else:
        lines.append("| （暂无因子数据） | | | |")
    lines.extend(["", "### 6. 行动建议", ""])
    suggestions = []
    if low_conf:
        suggestions.append(
            "人工抽检 TOP 低置信度测试用例，补充明确的业务预期、风险假设与证据计划。"
        )
    if low_modules:
        suggestions.append(
            "优先为结构化率偏低的模块（例如 %s）补齐需求文档。"
            % "、".join(cat for cat, _, _ in low_modules[:2])
        )
    if top5 and top5[0][1] > 30:
        suggestions.append(
            "对耗时超过 30 秒的测试用例进行性能排查，考虑拆分业务意图或增加等待策略。"
        )
    worst_rate = worst[0][1] if worst else 1.0
    if worst_rate < 0.7 and worst:
        suggestions.append(
            "模块「%s」通过率偏低（%.1f%%），建议集中修复。"
            % (worst[0][0], worst_rate * 100)
        )
    if intents_with_conf and counter.get("low", 0) / len(intents_with_conf) > 0.3:
        suggestions.append(
            "整体低置信度测试用例占比较高，建议回归需求来源并完善断言覆盖。"
        )
    if not suggestions:
        suggestions.append("当前项目状态良好，可继续保持现有流程。")
    for idx, s in enumerate(suggestions[:5], 1):
        lines.append("- %s" % s)
    lines.append("")
    if project_name:
        dashboard_lines = []
        with guard("_generate_smart_insights: from src.extensions.metrics_collecto", log=_log):
            from src.extensions.metrics_collector import metrics_collector

            if hasattr(metrics_collector, "get_confidence_trend"):
                trend = metrics_collector.get_confidence_trend(project_name)
                svg_trend = _generate_learning_curve_svg(trend or [])
                if svg_trend and "暂无历史数据" not in svg_trend:
                    dashboard_lines.extend(["### 7.1 学习曲线", "", svg_trend, ""])
        with guard("_generate_smart_insights: from src.pipeline.steps.step4.op_cac", log=_log):
            from src.pipeline.steps.step4.op_cache import op_cache

            cache_stats = {}
            if hasattr(op_cache, "get_stats"):
                cache_stats = op_cache.get_stats(project_name) or {}
            svg_cache = _generate_cache_dashboard_svg(cache_stats or {})
            if svg_cache and "暂无缓存统计" not in svg_cache:
                dashboard_lines.extend(["### 7.2 缓存仪表盘", "", svg_cache, ""])
        if dashboard_lines:
            lines.extend(["### 7. 📊 智能仪表盘", ""])
            lines.extend(dashboard_lines)

    # ── ★ KEEP-09: 趋势对比（本次 vs 上次）──
    if project_name and all_intents:
        with guard("_generate_smart_insights: from src.core.db_manager import get_", log=_log):
            from src.core.db_manager import get_connection

            conn = get_connection()
            try:
                from src.pipeline.steps.step5._db_utils import _ensure_run_summary_table

                _ensure_run_summary_table(conn)
                # 查询上次执行（按 created_at 倒序，跳过本次 run_id）
                prev = conn.execute(
                    "SELECT run_id, total_cases, total_passed, total_failed, "
                    "avg_confidence, total_duration_sec, created_at "
                    "FROM run_summary WHERE project=%s AND run_id<>%s "
                    "ORDER BY created_at DESC LIMIT 1",
                    (project_name, run_id or ""),
                ).fetchall()
                if len(prev) >= 1:
                    prev_run = prev[0]
                    cur_total = len(all_intents)
                    # 当前通过/失败估算
                    cur_passed = sum(
                        1
                        for c in all_intents
                        if str(
                            c.get("verification_status") or c.get("status") or ""
                        ).upper()
                        in ("PASS", "PASSED", "OK")
                    )
                    cur_failed = cur_total - cur_passed
                    prev_total = int(prev_run.get("total_cases", 0) or 0)
                    prev_passed = int(prev_run.get("total_passed", 0) or 0)
                    prev_failed = int(prev_run.get("total_failed", 0) or 0)
                    prev_confidence = float(prev_run.get("avg_confidence", 0) or 0)
                    prev_duration = float(prev_run.get("total_duration_sec", 0) or 0)

                    lines.extend(["", "### 8. 📈 趋势对比（本次 vs 上次）", ""])
                    lines.append("| 指标 | 上次 | 本次 | 变化 |")
                    lines.append("|------|------|------|------|")
                    # 通过率
                    prev_pass_rate = (
                        round(prev_passed / max(prev_total, 1) * 100, 1)
                        if prev_total
                        else 0
                    )
                    cur_pass_rate = (
                        round(cur_passed / max(cur_total, 1) * 100, 1)
                        if cur_total
                        else 0
                    )
                    delta_pass = cur_pass_rate - prev_pass_rate
                    arrow_pass = (
                        "↑" if delta_pass > 0 else "↓" if delta_pass < 0 else "→"
                    )
                    color_pass = "🟢" if delta_pass >= 0 else "🔴"
                    lines.append(
                        "| 通过率 | %.1f%% | %.1f%% | %s %+.1f%% |"
                        % (prev_pass_rate, cur_pass_rate, arrow_pass, delta_pass)
                    )
                    # 测试用例数
                    delta_total = cur_total - prev_total
                    lines.append(
                        "| 测试用例总数 | %d | %d | %+d |"
                        % (prev_total, cur_total, delta_total)
                    )
                    # 失败数
                    delta_fail = cur_failed - prev_failed
                    lines.append(
                        "| 失败数 | %d | %d | %+d (%s) |"
                        % (
                            prev_failed,
                            cur_failed,
                            delta_fail,
                            color_pass if delta_fail <= 0 else "🔴",
                        )
                    )
                    # 置信度
                    cur_confidence_vals = (
                        [float(ci.get("confidence", 0)) for _, ci in intents_with_conf]
                        if intents_with_conf
                        else []
                    )
                    cur_avg_conf = (
                        round(
                            sum(cur_confidence_vals) / max(len(cur_confidence_vals), 1),
                            3,
                        )
                        if cur_confidence_vals
                        else 0
                    )
                    if prev_confidence > 0 and cur_avg_conf > 0:
                        delta_conf = round(cur_avg_conf - prev_confidence, 3)
                        arrow_conf = (
                            "↑" if delta_conf > 0 else "↓" if delta_conf < 0 else "→"
                        )
                        lines.append(
                            "| 平均置信度 | %.3f | %.3f | %s %+.3f |"
                            % (prev_confidence, cur_avg_conf, arrow_conf, delta_conf)
                        )
                    # 总耗时
                    cur_duration = sum(
                        float(c.get("duration", 0) or c.get("elapsed", 0) or 0)
                        for c in all_intents
                    )
                    if prev_duration > 0 and cur_duration > 0:
                        delta_dur = round(cur_duration - prev_duration, 1)
                        arrow_dur = "↓" if delta_dur <= 0 else "↑" + "⚠️"
                        lines.append(
                            "| 总耗时(秒) | %.1f | %.1f | %s %.1f |"
                            % (prev_duration, cur_duration, arrow_dur, delta_dur)
                        )
                    lines.append("")
            finally:
                conn.close()

    return "\n".join(lines)
