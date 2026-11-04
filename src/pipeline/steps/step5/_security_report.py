# -*- coding: utf-8 -*-
"""安全（security）step5 报告适配层（薄编排，零业务逻辑）。

闭环::

    step4 ``cross_cut_gates.run_security_gate`` 采集（OWASP 规则驱动，无 AI 依赖）
        → step4 ``_persist_cross_cut_to_store`` / ``_persist_cross_cut_security``
          落库 oracle_results（oracle_type='security'）
        → 本模块 ``build_security_report`` 从 oracle_results 聚合
        → ``render_security_section`` 渲染 Markdown

与 a11y 门禁链路（``_a11y_report.py``）同构：安全是纯「汇总统计」维度，不做跨 run
基线对比，只做本轮发现的聚合展示。规则依赖 SecurityScanner（SQLi/XSS/敏感信息/
安全头/CSRF/路径遍历/命令注入/IDOR），severity 分级 critical/high/medium/low。

整体 try-except：任何异常只 warning，**绝不阻断报告生成**。
"""

from __future__ import annotations

import json
import logging
from typing import Any
from src.core.severity import SEVERITY_ORDER as _SEVERITY_ORDER  # noqa: F401  词表唯一源

_log = logging.getLogger(__name__)

# 严重度展示顺序与中文标签
_SEVERITY_LABELS = {
    "critical": "严重",
    "high": "高",
    "medium": "中",
    "low": "低",
}


def _fetch_security_rows(project: str, run_id: str) -> list[dict]:
    """从 oracle_results 拉取本轮 security 明细（security 维度）。"""
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
                    "WHERE o.oracle_type='security' "
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
                    _log.debug("[security_report] conn.close: %s", _e)
    except Exception as e:  # noqa: BLE001
        _log.warning("[security_report] 拉取安全数据失败(不阻断): %s", str(e)[:120])
        return []


def build_security_report(project: str, run_id: str) -> dict[str, Any]:
    """聚合本轮 security 维度为报告 dict。

    返回:
        ``{cases, total, by_severity, findings, clean_cases, warn_cases, fail_cases}``；
        无 security 记录时返回 ``{}``。
    """
    rows = _fetch_security_rows(project, run_id)
    if not rows:
        return {}

    agg = {
        "cases": 0,
        "total": 0,
        "by_severity": {},
        "findings": [],
        "clean_cases": 0,
        "warn_cases": 0,
        "fail_cases": 0,
        "case_items": [],
        "authz_items": [],
    }
    for r in rows:
        d = r.get("detail") or {}
        agg["cases"] += 1
        _total = int(d.get("total") or 0)
        agg["total"] += _total
        verdict = str(r.get("verdict") or "").strip()
        if _total == 0:
            agg["clean_cases"] += 1
        elif verdict == "fail":
            agg["fail_cases"] += 1
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
                "verdict": verdict,
                "total": _total,
                "by_severity": d.get("by_severity") or {},
            }
        )
        # 【2026-09-05】authz 子维度：detail.authz 若含探针则入 authz_items。
        _az = d.get("authz")
        if isinstance(_az, dict) and _az.get("total"):
            agg["authz_items"].append(
                {
                    "case_id": r.get("case_id", ""),
                    "all_pass": bool(_az.get("all_pass")),
                    "total": int(_az.get("total") or 0),
                    "passed": int(_az.get("passed") or 0),
                    "failed": int(_az.get("failed") or 0),
                    "probes": _az.get("probes") or [],
                }
            )

    # 聚合后的 findings 截断，防止报告过长
    if len(agg["findings"]) > 200:
        agg["findings"] = agg["findings"][:200]
        agg["findings_truncated"] = True
    return agg


def render_security_section(report: dict[str, Any]) -> str:
    """把 ``build_security_report`` 的结果渲染为 Markdown 区块。"""
    if not report:
        return ""
    cases = int(report.get("cases") or 0)
    if not cases:
        return ""

    total = int(report.get("total") or 0)
    by_sev = report.get("by_severity") or {}
    sev_str = "; ".join(
        "%s=%d" % (_SEVERITY_LABELS.get(k, k), by_sev[k])
        for k in _SEVERITY_ORDER
        if by_sev.get(k)
    ) or "无"

    lines = [
        "## 安全（Security）",
        "",
        "> 基于 OWASP Top 10 规则驱动检测（SQL 注入 / XSS / 敏感信息 / 安全头 / CSRF / "
        "路径遍历 / 命令注入 / IDOR），无 AI 依赖。",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        "| 覆盖用例 | %d |" % cases,
        "| 发现总数 | %d |" % total,
        "| 严重度分布 | %s |" % sev_str,
        "| 干净(0 发现) | %d |" % int(report.get("clean_cases") or 0),
        "| 有发现(warn) | %d |" % int(report.get("warn_cases") or 0),
        "| 高/严重(warn→fail) | %d |" % int(report.get("fail_cases") or 0),
    ]

    case_items = report.get("case_items") or []
    if case_items:
        lines += [
            "",
            "### 用例级明细",
            "",
            "| 用例 | 执行结果 | 判定 | 发现数 | 严重度 |",
            "|------|----------|------|--------|--------|",
        ]
        for c in case_items:
            _c_sev = "; ".join(
                "%s=%d" % (_SEVERITY_LABELS.get(k, k), v)
                for k, v in sorted((c.get("by_severity") or {}).items())
            ) or "-"
            lines.append(
                "| %s | %s | %s | %d | %s |"
                % (c.get("case_id", "-"), c.get("status", "-"),
                   c.get("verdict", "-"), c.get("total", 0), _c_sev)
            )

    # 【2026-09-05】authz（越权/访问控制）子维度：从各用例 detail.authz 聚合渲染。
    authz_items = report.get("authz_items") or []
    if authz_items:
        lines += [
            "",
            "### 越权/访问控制（AuthZ）",
            "",
            "> 基于 RBAC 权限矩阵探针（allow/deny/allow_self_only），覆盖正向/负向/边界/"
            r'默认拒绝/水平越权/垂直越权，纯 requests、无 AI 依赖。',
            "",
            "| 用例 | 探针数 | 通过 | 未过 | 全部通过 |",
            "|------|--------|------|------|----------|",
        ]
        for a in authz_items:
            lines.append(
                "| %s | %d | %d | %d | %s |"
                % (a.get("case_id", "-"), a.get("total", 0), a.get("passed", 0),
                   a.get("failed", 0),
                   "是" if a.get("all_pass") else ("否" if a.get("total") else "-"))
            )
        _probe_rows = []
        for a in authz_items:
            for p in (a.get("probes") or []):
                _probe_rows.append(
                    "| %s | %s | %s | %s | %s | %s |"
                    % (p.get("id", "?"), p.get("role", "?"), p.get("method", "?"),
                       p.get("path", "?"), p.get("actual", "?"),
                       "通过" if p.get("pass") else "FAIL")
                )
        if _probe_rows:
            lines += [
                "",
                "**探针明细**",
                "",
                "| 探针 | 角色 | 方法 | 路径 | 实际 | 结果 |",
                "|------|------|------|------|------|------|",
            ]
            lines += _probe_rows[:60]

    findings = report.get("findings") or []
    if findings:
        lines += [
            "",
            "### 发现样本",
            "",
            "| 规则 | 类别 | 严重度 | 证据片段 |",
            "|------|------|--------|----------|",
        ]
        for f in findings[:50]:
            lines.append(
                "| %s | %s | %s | %s |"
                % (
                    f.get("rule_id", "?"),
                    f.get("category", "?"),
                    _SEVERITY_LABELS.get(f.get("severity", "?"), f.get("severity", "?")),
                    (f.get("evidence") or f.get("description") or "")[:60],
                )
            )
        if report.get("findings_truncated"):
            lines.append("| ... | (更多见数据库) | ... |")

    return "\n".join(lines)


def build_security_section(project: str, run_id: str) -> str:
    """供 ``report_generator`` 调用：返回 security Markdown 区块。

    无数据或异常时返回 ``""``（调用方据此不渲染区块）。
    """
    try:
        report = build_security_report(project, run_id)
        if not report:
            return ""
        return render_security_section(report)
    except Exception as e:  # noqa: BLE001
        _log.warning("[security_report] 区块渲染失败(不阻断): %s", str(e)[:160])
        return ""


__all__ = ["build_security_report", "render_security_section", "build_security_section"]