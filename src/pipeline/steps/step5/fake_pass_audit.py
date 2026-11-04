# -*- coding: utf-8 -*-
"""假 PASS 审计器（W4-A，P0，用户红线①）—— 纯读聚合 executions，零 DB 写（默认 dry_run）。

判定口径（假绿审计·正确口径，2026-08-29 起与 audit.is_fake_pass 解耦并细分）：
  仅「无证据」PASS = status ∈ ('PASS','PASS_WITH_SLOW_BUG') 且 expected+actual+details 全空
    → 真正的假 PASS（自证通过却无任何支撑），记入 fake_passes/rate/hits（可拦截）。
  有证据但 confidence<0.70 的 PASS = 真实验证只是把握不高 → 单列 low_confidence_review
    （需人工复核），**不计入假绿率**，避免 4.76% 这类误标。

职责：
  - audit_fake_passes(project, run_id)：扫描 executions 中 status ∈ ('PASS','PASS_WITH_SLOW_BUG') 行 → 命中明细
  - summarize_fake_pass_audit(report)：报告文本（命中明细 + 假 PASS 率 + 阈值告警 ⚠️）
  - enforce_fake_pass_block(project, run_id, dry_run)：可选拦截——仅当
    FAKE_PASS_AUDIT_ENFORCE=True 且 dry_run=False 才改 executions 行（status→FAIL）；
    默认 dry_run 只报告，与 audit.py 映射层拦截（只改显示）职责分离。

零 DDL：只读现有 executions 列（status/confidence/expected/actual/details）。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

from src.core.pipeline_config import _read_config_value as _cfg

_log = logging.getLogger(__name__)

_EVIDENCE_FIELDS = ("expected", "actual", "details")


def _classify_pass(confidence, expected="", actual="", details="") -> str:
    """把一条 PASS 归类为 'ok' | 'low_confidence' | 'no_evidence'（假绿审计正确口径）。

    判定逻辑（区别于 audit.is_fake_pass 的"从宽关注"布尔口径）：
      - no_evidence : expected/actual/details 三列全空 → 真实验证无任何支撑，自称通过，
                     即"伪造/假绿"，严重缺陷。
      - low_confidence: 有实质证据但 confidence<0.70 → 真实验证(有 expected/actual/details
                      轨迹)只是把握不高，属"通过但需人工复核"，不是造假。
      - ok        : 有证据且置信度达标。

    关键：把两类本质不同的结果分开，避免把"低置信带证据的真实通过"误标为假绿。
    """
    ev_empty = not (expected or actual or details)
    if ev_empty:
        return "no_evidence"
    try:
        conf = float(confidence or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return "low_confidence" if conf < C.FAKE_PASS_LOW_CONF else "ok"


class _C:
    FAKE_PASS_LOW_CONF = 0.70
    """假绿判定中"低置信"阈值，与 audit.is_fake_pass / pipeline_config 保持一致。"""


C = _C()


def _evidence_gaps(row: dict) -> list[str]:
    """命中行的缺失项：confidence<0.7 记 'confidence'；三字段为空记对应字段。"""
    gaps: list[str] = []
    try:
        conf = float(row.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < 0.7:
        gaps.append("confidence")
    for field in _EVIDENCE_FIELDS:
        if not (row.get(field) or ""):
            gaps.append(field)
    return gaps


def audit_fake_passes(project: str, run_id: str = "") -> dict[str, Any]:
    """扫描 executions 中 status ∈ ('PASS','PASS_WITH_SLOW_BUG') 的行 → 判定假 PASS（纯读，零写）。

    参数:
        project: 项目名（必填）。
        run_id: 按 run_id 过滤（默认空=全量）。

    返回:
        {project, total_passes, fake_passes, rate, alert, threshold,
         hits: [{exec_id, case_id, confidence, evidence_gaps}]}
    """
    project = str(project or "").strip()
    report: dict[str, Any] = {
        "project": project,
        "total_passes": 0,
        "no_evidence_passes": 0,  # 真·无证据假绿（expected+actual+details 全空）
        "low_confidence_review": 0,  # 有证据但置信度<0.70：真通过，仅需人工复核
        "fake_passes": 0,  # 兼容旧字段，= no_evidence_passes
        "rate": 0.0,  # 假绿率 = no_evidence / total（仅真正无证据，不再被低置信带证据稀释）
        "alert": False,
        "threshold": 0.05,
        "hits": [],  # 仅 no_evidence 命中（可执行拦截的对象）
        "reviews": [],  # low_confidence 复核清单（不视为造假）
    }
    try:
        report["threshold"] = float(_cfg("FAKE_PASS_ALERT_RATE", 0.05) or 0.05)
    except (TypeError, ValueError):
        report["threshold"] = 0.05
    if not project:
        return report
    with guard("audit_fake_passes: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection

        conn = None
        try:
            conn = get_connection()
            if run_id:
                rows = (
                    conn.execute(
                        "SELECT id, case_id, confidence, expected, actual, details "
                        "FROM executions WHERE project=%s AND run_id=%s "
                        "AND status IN ('PASS','PASS_WITH_SLOW_BUG')",
                        (project, run_id),
                    ).fetchall()
                    or []
                )
            else:
                rows = (
                    conn.execute(
                        "SELECT id, case_id, confidence, expected, actual, details "
                        "FROM executions WHERE project=%s "
                        "AND status IN ('PASS','PASS_WITH_SLOW_BUG')",
                        (project,),
                    ).fetchall()
                    or []
                )
            for row in rows:
                report["total_passes"] += 1
                cls = _classify_pass(
                    row.get("confidence"),
                    row.get("expected"),
                    row.get("actual"),
                    row.get("details"),
                )
                if cls == "no_evidence":
                    report["no_evidence_passes"] += 1
                    report["fake_passes"] += 1
                    report["hits"].append(
                        {
                            "exec_id": row.get("id"),
                            "case_id": str(row.get("case_id", "") or ""),
                            "confidence": row.get("confidence"),
                            "classification": cls,
                            "evidence_gaps": _evidence_gaps(row),
                        }
                    )
                elif cls == "low_confidence":
                    report["low_confidence_review"] += 1
                    report["reviews"].append(
                        {
                            "exec_id": row.get("id"),
                            "case_id": str(row.get("case_id", "") or ""),
                            "confidence": row.get("confidence"),
                            "classification": cls,
                        }
                    )
            total = report["total_passes"]
            # 假绿率仅统计真正的 no_evidence（无证据自证通过）；低置信带证据不再计入。
            report["rate"] = (report["fake_passes"] / total) if total else 0.0
            report["alert"] = report["rate"] > report["threshold"]
        finally:
            if conn:
                with guard("audit_fake_passes: conn.close()", log=_log, level=logging.WARNING):
                    conn.close()
    return report


def summarize_fake_pass_audit(report: dict) -> str:
    """报告文本：命中明细 + 假 PASS 率 + 阈值告警 ⚠️（供 Step5 区块与 CLI 打印）。"""
    if not report:
        return ""
    total = int(report.get("total_passes", 0) or 0)
    if total <= 0:
        return ""
    fake = int(report.get("fake_passes", 0) or 0)
    review = int(report.get("low_confidence_review", 0) or 0)
    rate = float(report.get("rate", 0.0) or 0.0)
    threshold = float(report.get("threshold", 0.05) or 0.05)
    alert = bool(report.get("alert", False))
    lines = [
        "## 假 PASS 审计",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        "| PASS 总数 | %d |" % total,
        "| 无证据假 PASS（真·假绿） | %d |" % fake,
        "| 低置信但带证据（需人工复核） | %d |" % review,
        "| 假 PASS 率（无证据/总数） | %.2f%% |" % (rate * 100),
        "| 告警阈值 | %.2f%% |" % (threshold * 100),
        "| 状态 | %s |" % ("⚠️ RED（超阈值）" if alert else "✅ GREEN"),
    ]
    if alert:
        lines.append("")
        lines.append(
            "⚠️ **假 PASS 率超过阈值（%.2f%% > %.2f%%）**，存在无证据自证通过的用例，需处理。"
            % (rate * 100, threshold * 100)
        )
    hits = report.get("hits") or []
    if hits:
        lines += [
            "",
            "### 无证据假 PASS（命中明细）",
            "",
            "| exec_id | case_id | confidence | 缺失项 |",
            "|---------|---------|-----------|--------|",
        ]
        for h in hits[:50]:
            lines.append(
                "| %s | %s | %s | %s |"
                % (
                    h.get("exec_id", ""),
                    h.get("case_id", ""),
                    h.get("confidence", ""),
                    ",".join(h.get("evidence_gaps", []) or []),
                )
            )
        if len(hits) > 50:
            lines.append("")
            lines.append("... 共 %d 条命中，仅展示前 50 条。" % len(hits))
    reviews = report.get("reviews") or []
    if reviews:
        lines += [
            "",
            "### 低置信带证据（需人工复核，不计假绿）",
            "",
            "| exec_id | case_id | confidence |",
            "|---------|---------|-----------|",
        ]
        for rv in reviews[:50]:
            lines.append(
                "| %s | %s | %s |"
                % (rv.get("exec_id", ""), rv.get("case_id", ""), rv.get("confidence", ""))
            )
        if len(reviews) > 50:
            lines.append("")
            lines.append("... 共 %d 条复核项，仅展示前 50 条。" % len(reviews))
    return "\n".join(lines)


def enforce_fake_pass_block(
    project: str, run_id: str = "", dry_run: bool = True
) -> dict:
    """可选拦截：dry_run=False 且 FAKE_PASS_AUDIT_ENFORCE=True 时，对命中行
    update_execution_status → 'FAIL'（审计拦截，区别于 audit.py 映射层只改显示）。

    默认 dry_run=True 只报告，不改原始行。返回 report + enforced/enforced_ids。
    """
    report = audit_fake_passes(project, run_id)
    if dry_run or not bool(_cfg("FAKE_PASS_AUDIT_ENFORCE", False)):
        report["enforced"] = False
        report["enforced_ids"] = []
        return report
    enforced_ids: list = []
    for h in report.get("hits", []):
        exec_id = h.get("exec_id")
        if not exec_id:
            continue
        with guard("enforce_fake_pass_block: from src.users.audit import update_e", log=_log, level=logging.WARNING):
            from src.users.audit import update_execution_status

            if update_execution_status(
                int(exec_id),
                "FAIL",
                "假PASS审计拦截：无证据自证通过（expected/actual/details 三列全空）",
            ):
                enforced_ids.append(exec_id)
    report["enforced"] = True
    report["enforced_ids"] = enforced_ids
    return report
