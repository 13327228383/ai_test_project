# -*- coding: utf-8 -*-
"""Step5 报告新区块（W3 T02/T05）—— 归因 / 幂等 / 性能 区块渲染。

零 DDL：全部纯读/纯函数；开关默认 OFF → 默认不渲染（零行为变更）。
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


def render_attribution_section(attribution: dict) -> str:
    """把归因报告渲染为 Markdown 区块。"""
    if not attribution:
        return ""
    total = attribution.get("total", 0)
    if not total:
        return ""
    passed = attribution.get("passed", 0)
    blocked = attribution.get("blocked", 0)
    pre_gate = attribution.get("pre_gate_blocked", 0)
    denom = attribution.get("tpr_denominator", total)
    rate = (passed / denom * 100) if denom else 0.0
    lines = [
        "## 失败归因分布",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        "| 执行总数 | %d |" % total,
        "| 通过 | %d |" % passed,
        "| 失败(FAIL/ERROR) | %d |" % attribution.get("failed", 0),
        "| BLOCKED | %d |" % blocked,
        "| 预检阻断 | %d |" % pre_gate,
        "| 通过率 | %.1f%% |" % rate,
    ]
    by_cat = attribution.get("by_category") or {}
    if by_cat:
        lines += ["", "### 失败类别", "", "| 类别 | 次数 |", "|------|------|"]
        for cat, cnt in sorted(by_cat.items(), key=lambda kv: -kv[1]):
            lines.append("| %s | %d |" % (cat, cnt))
    reasons = attribution.get("blocked_reasons") or []
    if reasons:
        lines += ["", "### 阻断原因", ""]
        for r in reasons[:10]:
            lines.append(
                "- [%s] %s" % (r.get("code", "OTHER"), r.get("reason", "")[:80])
            )
    return "\n".join(lines)


def render_idempotency_section(results: list[dict]) -> str:
    """幂等区块渲染（summarize_idempotency 的 Markdown）。"""
    if not results:
        return ""
    from .idempotency import summarize_idempotency

    return summarize_idempotency(results)


def render_perf_section(baseline: dict) -> str:
    """性能基线区块渲染。"""
    if not baseline:
        return ""
    lines = [
        "## 性能基线",
        "",
        "| URL | 当前均值(ms) | 基线均值(ms) | p50 | p95 | 判定 |",
        "|-----|------------|-------------|-----|-----|------|",
    ]
    for item in baseline.get("items", [])[:20]:
        lines.append(
            "| %s | %s | %s | %s | %s | %s |"
            % (
                str(item.get("url", ""))[:40],
                item.get("current_mean", "-"),
                item.get("baseline_mean", "-"),
                item.get("p50", "-"),
                item.get("p95", "-"),
                item.get("verdict", "-"),
            )
        )
    return "\n".join(lines)


def render_perf_trend_section(trend: dict) -> str:
    """跨运行性能趋势区块渲染。

    数据源：``TrendAnalyzer.analyze_all()``（读 ``data/perf_baseline/perf_*.json``）。
    形态：汇总各用例跨 N 次运行的耗时变化（变慢 / 变快 / 稳定 / 数据不足），
    供人工一眼定位「持续变慢」的用例（性能退化预警）。纯函数，无可写依赖。
    """
    if not trend:
        return ""
    if trend.get("error") == "no_history":
        return ""
    total_cases = trend.get("total_cases", 0)
    total_runs = trend.get("total_runs", 0)
    if not total_cases:
        return ""
    trends = trend.get("trends") or {}
    degrading = trends.get("degrading", [])
    improving = trends.get("improving", [])
    stable = trends.get("stable", [])
    insufficient = trends.get("insufficient_data", [])

    lines = [
        "## 性能跨运行趋势",
        "",
        "基于最近 %d 次运行、%d 个用例的耗时对比（最近3次 vs 最早3次平均）。"
        % (total_runs, total_cases),
        "",
        "| 趋势 | 用例数 | 用例 |",
        "|------|-------|------|",
        "| 持续变慢 (退化) | %d | %s |" % (len(degrading), ", ".join(degrading) or "-"),
        "| 持续变快 (改善) | %d | %s |" % (len(improving), ", ".join(improving) or "-"),
        "| 稳定 | %d | %s |" % (len(stable), ", ".join(stable) or "-"),
        "| 数据不足 | %d | %s |" % (len(insufficient), ", ".join(insufficient) or "-"),
    ]

    # 退化/改善用例明细（有可量化数据才列出）
    details = trend.get("details") or {}
    detail_rows = []
    for _cid in list(degrading) + list(improving):
        _d = details.get(_cid) or {}
        if _d.get("data_points", 0) >= 2 and "ratio" in _d:
            detail_rows.append(
                "| %s | %s | %.1f | %.1f | x%.2f |"
                % (
                    _cid,
                    _d.get("trend", ""),
                    _d.get("recent_avg_ms", 0.0),
                    _d.get("earliest_avg_ms", 0.0),
                    _d.get("ratio", 1.0),
                )
            )
    if detail_rows:
        lines += [
            "",
            "### 已量化的退化/改善用例",
            "",
            "| 用例 | 趋势 | 近期均值(ms) | 早期均值(ms) | 变比 |",
            "|------|------|------------|------------|------|",
        ]
        lines += detail_rows
    return "\n".join(lines)


#: 折线图配色（色阶，稳定可分辨）
_TREND_COLORS = ("#e3462f", "#2f8ae3", "#3ba55d", "#b078e0", "#e08a2f", "#2fb5a5")


def render_perf_trend_svg(series: dict) -> str:
    """把用例耗时序列渲染为**单行**内联 SVG 折线图（数据可视化升级）。

    - 特征：纯文本 SVG（无外部依赖/无 JS）、确定性、可嵌入 markdown；
      report_generator 的 HTML 包装器会把它原样嵌入 ``<p>``，浏览器直接渲染折线。
    - 输入：``{case_id: [{"elapsed_ms": .., "run_id": .., "timestamp": ..}, ...]}``，
      序列正序（早期 → 近期）。
    - 图例列于图底部横排；y 轴为耗时(ms)网格。横坐标=各次运行（同名序次）。
    """
    if not series:
        return ""
    # 仅对 >=2 个采样点的用例绘制（单点无法构成线）
    seq = {cid: pts for cid, pts in series.items() if len(pts) >= 2}
    if not seq:
        return ""
    cases = list(seq.keys())[:6]  # 最多绘 6 条，避免图表过密

    W, H, L, R, T, B = 940, 250, 52, 16, 20, 30
    pw, ph = W - L - R, H - T - B

    all_v = [p["elapsed_ms"] for c in cases for p in seq[c]]
    vmin, vmax = float(min(all_v)), float(max(all_v))
    if vmax <= vmin:
        vmax = vmin + 1.0
    pad = (vmax - vmin) * 0.08
    vmax += pad
    vmin = max(0.0, vmin - pad)

    max_n = max(len(seq[c]) for c in cases)

    def _x(i: int) -> float:
        return L + pw * (i / (max_n - 1) if max_n > 1 else 0.0)

    def _y(v: float) -> float:
        return T + ph * (1.0 - (v - vmin) / (vmax - vmin))

    parts = ["<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"%d\" height=\"%d\" "
             "viewBox=\"0 0 %d %d\">" % (W, H, W, H)]

    # 标题
    parts.append("<text x=\"%d\" y=\"12\" font-size=\"11\" fill=\"#444\">"
                 "性能耗时趋势 (ms，各运行推进，早期→近期)</text>" % L)

    # 网格 + y 轴刻度（纵坐标：耗时 ms）
    grid = 4
    for gi in range(grid + 1):
        val = vmax - (vmax - vmin) * gi / grid
        gy = T + ph * gi / grid
        parts.append("<line x1=\"%d\" y1=\"%.1f\" x2=\"%d\" y2=\"%.1f\" stroke=\"#eee\"/>"
                     % (L, gy, W - R, gy))
        parts.append("<text x=\"%d\" y=\"%.1f\" font-size=\"9\" fill=\"#888\" "
                     "text-anchor=\"end\">%.0f</text>" % (L - 4, gy + 3, val))

    # 折线 + 数据点
    for idx, cid in enumerate(cases):
        color = _TREND_COLORS[idx % len(_TREND_COLORS)]
        pts = seq[cid]
        poly = " ".join(
            "%.1f,%.1f" % (_x(i), _y(p["elapsed_ms"])) for i, p in enumerate(pts)
        )
        parts.append("<polyline points=\"%s\" fill=\"none\" stroke=\"%s\" "
                     "stroke-width=\"1.6\"/>" % (poly, color))
        for i, p in enumerate(pts):
            parts.append("<circle cx=\"%.1f\" cy=\"%.1f\" r=\"2.4\" fill=\"%s\"/>"
                         % (_x(i), _y(p["elapsed_ms"]), color))

    # 图例（图底部横排）
    lx = L
    for idx, cid in enumerate(cases):
        color = _TREND_COLORS[idx % len(_TREND_COLORS)]
        label = cid[:16]
        parts.append("<rect x=\"%.0f\" y=\"%d\" width=\"9\" height=\"9\" fill=\"%s\"/>"
                     % (lx, H - B + 2, color))
        parts.append("<text x=\"%.0f\" y=\"%d\" font-size=\"9\" fill=\"#555\">%s</text>"
                     % (lx + 11, H - B + 10, label))
        lx += 13 + len(label) * 5.5 + 14

    parts.append("</svg>")
    return "".join(parts)


def render_bug_report_section(project: str, run_id: str = "") -> str:
    """Bug 清单/缺陷区段渲染（severity 分级 + 最小复现 + 可提单性）。

    死代码修复接线（2026-09-04）：``_bug_report.build_bug_report(..., with_tickets=True)``
    此前从未被报告生成流程调用（连同 bug_ticket / minimal_repro 整链死代码）。本函数把
    FAIL/ERROR=Bug、SKIP=跳过 的产出渲染进主报告（report_generator 拼接 ``lines`` 后
    同时出现在 Markdown 与 HTML）。

    fail-open：无 Bug 时如实显示"无缺陷"，数据源异常时返回"数据不可用"提示并注明，
    绝不造假、绝不打破主报告流程。DB 连接在函数内获得/归还，调用方无需关心。
    """
    try:
        from src.core.db_manager import get_connection
        from src.pipeline.steps.step5._bug_report import build_bug_report

        conn = None
        try:
            conn = get_connection()
            report = build_bug_report(
                conn, project=project, limit=100, with_tickets=True
            )
        finally:
            if conn:
                try:
                    conn.close()
                except (OSError, ConnectionError) as _e:
                    _log.debug("render_bug_report_section best-effort 容忍: %s", str(_e)[:120])
    except Exception as _e:  # noqa: BLE001 —— 接线须 fail-open
        _log.warning("[bug_report] Bug 区段生成失败(不阻断): %s", str(_e)[:120])
        return "\n".join(
            [
                "## Bug 清单 / 缺陷",
                "",
                "⚠️ 缺陷数据不可用（数据源读取异常），未失真渲染。",
                "",
            ]
        )

    bugs = report.get("bugs") or []
    tickets = report.get("bug_tickets") or {}
    ticket_rows = {str((t or {}).get("ticket_id")): t for t in (tickets.get("tickets") or [])
                   if isinstance(t, dict)}
    lines = ["## Bug 清单 / 缺陷", ""]
    if not bugs:
        lines += [
            "✅ 无缺陷（executions 中无 FAIL/ERROR 记录）。",
            "",
        ]
        _skipped = int(report.get("skipped_total", 0) or 0)
        if _skipped:
            lines.append("- 跳过用例（非缺陷，单列）: %d 条。" % _skipped)
        lines.append("")
        return "\n".join(lines)

    dist = report.get("severity_distribution") or {}
    lines += [
        "| 严重级别 | 数量 |",
        "|------|------|",
        "| 高 / high | %d |" % int(dist.get("high", 0) or 0),
        "| 中 / medium | %d |" % int(dist.get("medium", 0) or 0),
        "| 低 / low | %d |" % int(dist.get("low", 0) or 0),
        "",
    ]
    if int(tickets.get("ticket_total", 0) or 0) > 0:
        lines.append(
            "- **可提单缺陷单**: %d 条（自动可提单 %d / 人工复核 %d）。"
            % (
                int(tickets.get("ticket_total", 0) or 0),
                int(tickets.get("auto_fileable_total", 0) or 0),
                int(tickets.get("manual_review_total", 0) or 0),
            )
        )
        lines.append("")

    for idx, b in enumerate(bugs, 1):
        severity = str(b.get("severity") or "medium")
        case_id = str(b.get("case_id") or "?")
        title = str(b.get("title") or "[%s] %s" % (severity, case_id))
        lines += ["### %d. %s" % (idx, title), ""]
        lines.append(
            "- **严重级别**: %s 　**用例**: %s 　**所属**: %s"
            % (severity, case_id, str(b.get("check_type") or "-"))
        )
        triage = (
            b.get("failure_triage") if isinstance(b.get("failure_triage"), dict) else {}
        )
        root_cause = (
            str(b.get("failure_reason") or "")[:100]
            or str(triage.get("category") or "UNKNOWN")
        )
        lines.append(
            "- **失败归属**: %s 　**根因**: %s"
            % (str(triage.get("category") or "UNKNOWN"), root_cause)
        )
        # 最小复现（D3 收敛清单；无可复现步骤则如实标注）
        mr = b.get("minimal_repro") if isinstance(b.get("minimal_repro"), dict) else {}
        if mr.get("has_repro"):
            ksteps = [s for s in (mr.get("key_steps") or []) if isinstance(s, dict)]
            lines.append(
                "- **最小复现**（%d 步，收敛率 %.0f%%）：%s"
                % (
                    int(mr.get("key_step_count") or len(ksteps)),
                    float(mr.get("convergence_ratio") or 0.0) * 100,
                    " → ".join(str(s.get("action_desc", "")) for s in ksteps) or "-",
                )
            )
        else:
            lines.append("- **最小复现**: 无可回放步骤（如实标注）。")
        # 可提单性（对应当前 bug 的缺陷单，按 ticket_id=BUG-<execution_id> 匹配）
        want = "BUG-%s" % (b.get("execution_id") or "UNKNOWN")
        t = ticket_rows.get(want)
        if t:
            lines.append(
                "- **可提单**: %s 　**优先级**: %s 　**影响面**: %s"
                % (
                    "是" if t.get("auto_fileable") else "否",
                    str(t.get("priority") or "-"),
                    str(t.get("impact") or "-"),
                )
            )
            blockers = t.get("file_blockers") or []
            if blockers:
                lines.append("  - 提单阻塞：%s" % "；".join(str(x) for x in blockers))
        lines.append("")

    _skipped = int(report.get("skipped_total", 0) or 0)
    if _skipped:
        lines += ["---", "", "- **跳过用例（非缺陷，单列）**: %d 条。" % _skipped, ""]
    return "\n".join(lines)
