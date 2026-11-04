# -*- coding: utf-8 -*-
"""失败归因聚合（W3 T02，L0-1 消费闭环）—— 纯读聚合，零 DB 写。

职责：
  - build_attribution_report：从 executions 聚合 status/failure_category 分布，
    BLOCKED / pre_gate_blocked 单列，通过率 = passed / (total - blocked - pre_gate_blocked)
    （ATTRIBUTION_STRICT_TPR 开启时生效；默认保留原口径）。
  - classify_pre_gate_reason：对 PRE_GATE_BLOCKED 的 failure_reason 做关键词归因
    （纯函数，零 DB 写 —— 归因码只在报告层呈现，不改历史行）。
  - enrich_report_with_attribution：把归因区块并入 Step5 stats。

零 DDL：不建表、不 ALTER；所有数据来自 executions 现有列。
"""
from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

_log = logging.getLogger(__name__)

# PRE_GATE_BLOCKED reason 关键词 → 结构化归因码
_PRE_GATE_KEYWORD_MAP: list[tuple[str, str]] = [
    # 定位器不可解析（智能定位/元素定位失败）
    ("定位", "LOCATOR_UNRESOLVABLE"),
    ("locator", "LOCATOR_UNRESOLVABLE"),
    ("元素", "LOCATOR_UNRESOLVABLE"),
    ("selector", "LOCATOR_UNRESOLVABLE"),
    # 数据缺失（前置数据/种子数据不满足）
    ("数据", "SETUP_UNSATISFIABLE"),
    ("data", "SETUP_UNSATISFIABLE"),
    ("setup", "SETUP_UNSATISFIABLE"),
    ("种子", "SETUP_UNSATISFIABLE"),
    # 权限不足（身份/账号问题）
    ("权限", "IDENTITY_UNAVAILABLE"),
    ("身份", "IDENTITY_UNAVAILABLE"),
    ("账号", "IDENTITY_UNAVAILABLE"),
    ("登录", "IDENTITY_UNAVAILABLE"),
    ("login", "IDENTITY_UNAVAILABLE"),
    # 建模缺失（需求/用例/页面建模缺失）
    ("建模", "MODEL_DATA_MISSING"),
    ("页面", "MODEL_DATA_MISSING"),
    ("model", "MODEL_DATA_MISSING"),
    ("schema", "MODEL_DATA_MISSING"),
]


def classify_pre_gate_reason(reason_text: str) -> str:
    """对 PRE_GATE_BLOCKED 的 failure_reason 做关键词归因。

    定位器不可解析→LOCATOR_UNRESOLVABLE / 数据缺失→SETUP_UNSATISFIABLE /
    权限不足→IDENTITY_UNAVAILABLE / 建模缺失→MODEL_DATA_MISSING，默认 OTHER。
    纯函数，零 DB 写。
    """
    text = str(reason_text or "").lower()
    if not text.strip():
        return "OTHER"
    for keyword, code in _PRE_GATE_KEYWORD_MAP:
        if keyword in text:
            return code
    return "OTHER"


def build_attribution_report(project: str, run_id: str = "") -> dict[str, Any]:
    """SELECT status, failure_category, COUNT(*) FROM executions GROUP BY ... 聚合。

    返回 {total, passed, failed, error, blocked, pre_gate_blocked,
          by_category: {PRODUCT_BUG:n,...}, blocked_reasons: [...]}
    ★ 口径：通过率 = passed / (total - blocked - pre_gate_blocked)
      （ATTRIBUTION_STRICT_TPR=True 时剔除；默认保留原口径 total）。
    """
    report: dict[str, Any] = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "error": 0,
        "blocked": 0,
        "pre_gate_blocked": 0,
        "by_category": {},
        "blocked_reasons": [],
        "strict_tpr": False,
        "tpr_denominator": 0,
    }
    try:
        from src.core.pipeline_config import _read_config_value as _cfg

        strict_tpr = bool(_cfg("ATTRIBUTION_STRICT_TPR", False))
        report["strict_tpr"] = strict_tpr
    except Exception:  # noqa: BLE001
        strict_tpr = False

    try:
        from src.core.db_manager import get_connection

        conn = None
        try:
            conn = get_connection()
            # 1) status 分布
            if run_id:
                rows = (
                    conn.execute(
                        "SELECT status, COUNT(*) AS cnt FROM executions "
                        "WHERE project=%s AND run_id=%s GROUP BY status",
                        (project, run_id),
                    ).fetchall()
                    or []
                )
            else:
                rows = (
                    conn.execute(
                        "SELECT status, COUNT(*) AS cnt FROM executions "
                        "WHERE project=%s GROUP BY status",
                        (project,),
                    ).fetchall()
                    or []
                )
            status_map: dict[str, int] = {}
            for r in rows:
                raw = str(r.get("status", "") or "").strip().upper()
                status_map[raw] = status_map.get(raw, 0) + int(r.get("cnt", 0) or 0)
            total = sum(status_map.values())
            report["total"] = total
            report["passed"] = status_map.get("PASS", 0) + status_map.get(
                "PASS_WITH_SLOW_BUG", 0
            )
            report["failed"] = status_map.get("FAIL", 0) + status_map.get("ERROR", 0)
            report["blocked"] = status_map.get("BLOCKED", 0)
            report["pre_gate_blocked"] = status_map.get("PRE_GATE_BLOCKED", 0)

            # 2) failure_category 分布（失败/错误行）
            if run_id:
                cat_rows = (
                    conn.execute(
                        "SELECT failure_category, COUNT(*) AS cnt FROM executions "
                        "WHERE project=%s AND run_id=%s AND failure_category != '' "
                        "GROUP BY failure_category",
                        (project, run_id),
                    ).fetchall()
                    or []
                )
            else:
                cat_rows = (
                    conn.execute(
                        "SELECT failure_category, COUNT(*) AS cnt FROM executions "
                        "WHERE project=%s AND failure_category != '' "
                        "GROUP BY failure_category",
                        (project,),
                    ).fetchall()
                    or []
                )
            by_category: dict[str, int] = {}
            for r in cat_rows:
                cat = str(r.get("failure_category", "") or "").strip()
                if cat:
                    by_category[cat] = by_category.get(cat, 0) + int(
                        r.get("cnt", 0) or 0
                    )
            report["by_category"] = by_category

            # 3) blocked reasons 摘要（status ∈ BLOCKED / PRE_GATE_BLOCKED 的 failure_reason）
            if run_id:
                br_rows = (
                    conn.execute(
                        "SELECT status, failure_reason FROM executions "
                        "WHERE project=%s AND run_id=%s AND status IN ('BLOCKED','PRE_GATE_BLOCKED') "
                        "AND failure_reason != '' LIMIT 100",
                        (project, run_id),
                    ).fetchall()
                    or []
                )
            else:
                br_rows = (
                    conn.execute(
                        "SELECT status, failure_reason FROM executions "
                        "WHERE project=%s AND status IN ('BLOCKED','PRE_GATE_BLOCKED') "
                        "AND failure_reason != '' LIMIT 100",
                        (project,),
                    ).fetchall()
                    or []
                )
            blocked_reasons: list[dict[str, str]] = []
            for r in br_rows:
                status_raw = str(r.get("status", "") or "").strip().upper()
                reason = str(r.get("failure_reason", "") or "")[:200]
                code = classify_pre_gate_reason(reason)
                blocked_reasons.append(
                    {"status": status_raw, "reason": reason, "code": code}
                )
            report["blocked_reasons"] = blocked_reasons

            # 4) TPR 口径
            if strict_tpr:
                denom = total - report["blocked"] - report["pre_gate_blocked"]
            else:
                denom = total
            report["tpr_denominator"] = max(denom, 0)
        finally:
            if conn:
                try:
                    conn.close()
                except (OSError, ConnectionError) as _e:
                    _log.debug("[attribution] conn.close: %s", _e)
        return report
    except Exception as e:  # noqa: BLE001 —— 纯读失败不阻断报告
        _log.warning("[attribution] 聚合失败（返回空报告）: %s", e)
        return report


def enrich_report_with_attribution(stats: dict, project: str, run_id: str) -> dict:
    """把归因区块并入 Step5 stats；stats 无 project/run_id 时跳过（零行为变更）。

    仅当 ATTRIBUTION_ENABLED=True 且 project 非空才执行。
    """
    if not isinstance(stats, dict):
        return stats
    project = str(project or "").strip()
    if not project:
        return stats
    try:
        from src.core.pipeline_config import _read_config_value as _cfg

        if not bool(_cfg("ATTRIBUTION_ENABLED", False)):
            return stats
    except Exception:  # noqa: BLE001
        return stats
    with guard("enrich_report_with_attribution: report = build_attribution_report(pr", log=_log, level=logging.WARNING):
        report = build_attribution_report(project, run_id or "")
        stats["attribution"] = report
    return stats
