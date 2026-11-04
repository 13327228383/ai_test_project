# -*- coding: utf-8 -*-
"""可访问性（a11y）step5 报告适配层（薄编排，零业务逻辑）。

闭环::

    step4 ``_a11y_gate._flag_a11y`` 采集（8 规则，规则驱动无 AI 依赖）
        → step4 ``write_a11y_result_from_execution`` 落库 oracle_results
          （oracle_type='accessibility'）
        → 本模块 ``build_a11y_report`` 从 oracle_results 聚合
        → ``render_a11y_section`` 渲染 Markdown

与 perf 的门禁链路（``_perf_gate_report.py``）同构，但更简单：a11y 是纯「汇总统计」
维度，不做跨 run 基线对比，只做本轮发现的聚合展示。

整体 try-except：任何异常只 warning，**绝不阻断报告生成**。

[已复核-DeepSeek-V3 2026-08-25]
"""

from __future__ import annotations

import json
import logging
from typing import Any
from src.core.severity import SEVERITY_ORDER as _SEVERITY_ORDER  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)

# 严重度展示顺序与中文标签（与 src/extensions/a11y 的 SEVERITY_ORDER 对齐）
_SEVERITY_LABELS = {
    "critical": "严重",
    "high": "高",
    "medium": "中",
    "low": "低",
}


def _fetch_a11y_rows(project: str, run_id: str) -> list[dict]:
    """从 oracle_results 拉取本轮 a11y 明细（accessibility 维度）。"""
    project = str(project or "").strip()
    if not project:
        return []
    try:
        from src.core.db_manager import get_connection

        conn = None
        try:
            conn = get_connection()
            rows = (
                conn.execute(
                    "SELECT o.verdict, o.score, o.detail, "
                    "o.case_id AS o_case, o.run_id AS o_run, "
                    "e.case_id AS e_case, e.status "
                    "FROM oracle_results o "
                    "LEFT JOIN executions e ON e.id = o.execution_id "
                    "WHERE o.oracle_type='accessibility' "
                    "AND ( (o.execution_id = 0 AND o.run_id = %s) "
                    "      OR (o.execution_id > 0 AND e.id IS NOT NULL "
                    "          AND e.project=%s AND e.run_id=%s) ) "
                    "ORDER BY o.id ASC",
                    (str(run_id or ""), project, str(run_id or "")),
                ).fetchall()
                or []
            )
            out = []
            for r in rows:
                detail = r.get("detail")
                if isinstance(detail, str):
                    try:
                        detail = json.loads(detail)
                    except (ValueError, TypeError):
                        detail = None
                _det = detail if isinstance(detail, dict) else {}
                _is_run_level = not (r.get("e_case") or "")
                out.append(
                    {
                        "case_id": (r.get("e_case")
                                    or (r.get("o_case") or _det.get("case_id", ""))
                                    or ""),
                        "status": (r.get("status") or "") if not _is_run_level else "运行级",
                        "verdict": r.get("verdict", ""),
                        "score": float(r.get("score", 0) or 0),
                        "detail": _det,
                    }
                )
            return out
        finally:
            if conn:
                try:
                    conn.close()
                except (OSError, ConnectionError) as _e:
                    _log.debug("[a11y_report] conn.close: %s", _e)
    except Exception as e:  # noqa: BLE001
        _log.warning("[a11y_report] 拉取可访问性数据失败(不阻断): %s", str(e)[:120])
        return []


def build_a11y_report(project: str, run_id: str) -> dict[str, Any]:
    """聚合本轮 a11y 维度为报告 dict。

    返回:
        ``{cases, checked, skipped, total, by_severity, findings, pass_cases, warn_cases}``；
        无 accessibility 记录时返回 ``{}``。
    """
    rows = _fetch_a11y_rows(project, run_id)
    if not rows:
        return {}

    agg = {
        "cases": 0,
        "checked": 0,
        "skipped": 0,
        "total": 0,
        "by_severity": {},
        "findings": [],
        "pass_cases": 0,
        "warn_cases": 0,
        "case_items": [],
    }
    for r in rows:
        d = r.get("detail") or {}
        agg["cases"] += 1
        agg["checked"] += int(d.get("checked") or 0)
        agg["skipped"] += int(d.get("skipped") or 0)
        _total = int(d.get("total") or 0)
        agg["total"] += _total
        if _total == 0:
            agg["pass_cases"] += 1
        else:
            agg["warn_cases"] += 1
        for k, v in (d.get("by_severity") or {}).items():
            agg["by_severity"][k] = agg["by_severity"].get(k, 0) + int(v or 0)
        for f in (d.get("findings") or [])[:200]:
            if isinstance(f, dict):
                agg["findings"].append(f)
        agg["case_items"].append(
            {
                "case_id": r.get("case_id", ""),
                "status": r.get("status", ""),
                "total": _total,
                "by_severity": d.get("by_severity") or {},
            }
        )

    # 聚合后的 findings 截断，防止报告过长
    if len(agg["findings"]) > 200:
        agg["findings"] = agg["findings"][:200]
        agg["findings_truncated"] = True
    return agg


def render_a11y_section(report: dict[str, Any]) -> str:
    """把 ``build_a11y_report`` 的结果渲染为 Markdown 区块。"""
    if not report:
        return ""
    cases = int(report.get("cases") or 0)
    if not cases:
        return ""

    total = int(report.get("total") or 0)
    checked = int(report.get("checked") or 0)
    skipped = int(report.get("skipped") or 0)
    by_sev = report.get("by_severity") or {}
    sev_str = "; ".join(
        "%s=%d" % (_SEVERITY_LABELS.get(k, k), by_sev[k])
        for k in _SEVERITY_ORDER
        if by_sev.get(k)
    ) or "无"

    lines = [
        "## 可访问性（a11y）",
        "",
        "> 基于 8 条通用规则（img-alt / label 关联 / button 文本 / heading 跳级 / "
        "a-href / aria-label / 重复 id），规则驱动、无 AI 依赖。",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        "| 覆盖用例 | %d |" % cases,
        "| 检查页面 | %d |" % checked,
        "| 跳过(取 HTML 失败) | %d |" % skipped,
        "| 违规总数 | %d |" % total,
        "| 严重度分布 | %s |" % sev_str,
        "| 通过(0 违规) | %d |" % int(report.get("pass_cases") or 0),
        "| 有违规 | %d |" % int(report.get("warn_cases") or 0),
    ]

    case_items = report.get("case_items") or []
    if case_items:
        lines += [
            "",
            "### 用例级明细",
            "",
            "| 用例 | 执行结果 | 违规数 | 严重度 |",
            "|------|----------|--------|--------|",
        ]
        for c in case_items:
            _c_sev = "; ".join(
                "%s=%d" % (_SEVERITY_LABELS.get(k, k), v)
                for k, v in sorted((c.get("by_severity") or {}).items())
            ) or "-"
            lines.append(
                "| %s | %s | %d | %s |"
                % (c.get("case_id", "-"), c.get("status", "-"), c.get("total", 0), _c_sev)
            )

    findings = report.get("findings") or []
    if findings:
        lines += [
            "",
            "### 违规样本",
            "",
            "| 规则 | 严重度 | 定位片段 |",
            "|------|--------|----------|",
        ]
        for f in findings[:50]:
            lines.append(
                "| %s | %s | %s |"
                % (
                    f.get("rule", "?"),
                    _SEVERITY_LABELS.get(f.get("severity", "?"), f.get("severity", "?")),
                    (f.get("snippet") or f.get("element") or "")[:60],
                )
            )
        if report.get("findings_truncated"):
            lines.append("| ... | (更多见数据库) | ... |")

    return "\n".join(lines)


def build_a11y_section(project: str, run_id: str) -> str:
    """供 ``report_generator`` 调用：返回 a11y Markdown 区块。

    无数据或异常时返回 ``""``（调用方据此不渲染区块）。
    """
    try:
        report = build_a11y_report(project, run_id)
        if not report:
            return ""
        return render_a11y_section(report)
    except Exception as e:  # noqa: BLE001
        _log.warning("[a11y_report] 区块渲染失败(不阻断): %s", str(e)[:160])
        return ""


__all__ = ["build_a11y_report", "render_a11y_section", "build_a11y_section"]