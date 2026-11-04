# -*- coding: utf-8 -*-
"""幂等比对工具（W3 T02，L0-6）—— 纯函数比对，零 DB 写。

原料：executions(status/failure_category/failure_chain) + oracle_results.detail(JSON)。
职责：
  - compare_executions(a,b)：比对两条 executions 行 → 一致性报告 + evidence 摘要 hash。
  - compare_case_runs(project, case_id, run_a, run_b)：跨 run 拉取同一 case 的两条
    executions + 关联 oracle_results → compare_executions。
  - summarize_idempotency(results)：生成报告文本（一致率 / 不一致明细）。

零 DDL：不建表、不 ALTER；只读现有 executions / oracle_results 列。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

_log = logging.getLogger(__name__)

_COMPARE_FIELDS = ("status", "failure_category", "failure_reason")


def _norm_evidence(detail_raw: Any) -> str:
    """把 oracle_results.detail（JSON 字符串或 dict）归一为稳定 hash 原料。"""
    if isinstance(detail_raw, str):
        try:
            detail_raw = json.loads(detail_raw)
        except (ValueError, TypeError):
            return str(detail_raw)[:500]
    try:
        canonical = json.dumps(detail_raw, ensure_ascii=False, sort_keys=True)
    except (ValueError, TypeError):
        canonical = str(detail_raw)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def compare_executions(a: dict, b: dict) -> dict[str, Any]:
    """比对两条 executions 行。

    a/b 应含 status / failure_category / failure_reason / detail(可选)。
    返回 {identical, diff, evidence_hash_a, evidence_hash_b}
    """
    a = a or {}
    b = b or {}
    diff: list[dict[str, str]] = []
    for field in _COMPARE_FIELDS:
        va = str(a.get(field, "") or "").strip()
        vb = str(b.get(field, "") or "").strip()
        if va != vb:
            diff.append({"field": field, "a": va[:200], "b": vb[:200]})
    hash_a = _norm_evidence(a.get("detail"))
    hash_b = _norm_evidence(b.get("detail"))
    if hash_a != hash_b:
        diff.append({"field": "evidence_detail", "a": hash_a, "b": hash_b})
    return {
        "identical": len(diff) == 0,
        "diff": diff,
        "evidence_hash_a": hash_a,
        "evidence_hash_b": hash_b,
    }


def _fetch_execution(project: str, case_id: str, run_id: str) -> dict | None:
    """拉取一条 executions + 关联 oracle_results.detail。"""
    try:
        from src.core.db_manager import get_connection

        conn = None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT e.status, e.failure_category, e.failure_reason, "
                "(SELECT o.detail FROM oracle_results o WHERE o.execution_id=e.id "
                " ORDER BY o.id DESC LIMIT 1) AS detail "
                "FROM executions e WHERE e.project=%s AND e.case_id=%s AND e.run_id=%s "
                "ORDER BY e.executed_at DESC LIMIT 1",
                (project, case_id, run_id),
            ).fetchone()
            if not row:
                return None
            return {
                "status": row.get("status", ""),
                "failure_category": row.get("failure_category", ""),
                "failure_reason": row.get("failure_reason", ""),
                "detail": row.get("detail"),
            }
        finally:
            if conn:
                try:
                    conn.close()
                except (OSError, ConnectionError) as _e:
                    _log.warning("[idempotency] conn.close 失败: %s", _e)
    except Exception as e:  # noqa: BLE001
        _log.warning("[idempotency] 拉取执行记录失败: %s", e)
        return None


def compare_case_runs(
    project: str, case_id: str, run_a: str, run_b: str
) -> dict[str, Any]:
    """跨 run 拉取同一 case 的两条 executions + 关联 oracle_results → compare_executions。

    run_id 缺失 → 取该 case 最近两次执行（按 executed_at）。
    返回 {identical, diff, a, b, source}
    """
    project = str(project or "").strip()
    case_id = str(case_id or "").strip()
    if not project or not case_id:
        return {
            "identical": False,
            "diff": [{"field": "param", "a": "", "b": "project/case_id 缺失"}],
            "a": None,
            "b": None,
            "source": "param_error",
        }
    try:
        if run_a and run_b:
            a = _fetch_execution(project, case_id, run_a)
            b = _fetch_execution(project, case_id, run_b)
            source = "run_id"
        else:
            # 最近两次执行（无 run_id 指定）
            from src.core.db_manager import get_connection

            conn = None
            try:
                conn = get_connection()
                rows = (
                    conn.execute(
                        "SELECT run_id FROM executions WHERE project=%s AND case_id=%s "
                        "ORDER BY executed_at DESC LIMIT 2",
                        (project, case_id),
                    ).fetchall()
                    or []
                )
                run_ids = [
                    str(r.get("run_id", "") or "") for r in rows if r.get("run_id")
                ]
            finally:
                if conn:
                    try:
                        conn.close()
                    except (OSError, ConnectionError) as _e:
                        _log.warning("[idempotency] conn.close 失败: %s", _e)
            if len(run_ids) < 2:
                return {
                    "identical": False,
                    "diff": [{"field": "runs", "a": "", "b": "不足 2 次执行"}],
                    "a": None,
                    "b": None,
                    "source": "insufficient_runs",
                }
            a = _fetch_execution(project, case_id, run_ids[0])
            b = _fetch_execution(project, case_id, run_ids[1])
            source = "recent_two"
        if a is None or b is None:
            return {
                "identical": False,
                "diff": [{"field": "execution", "a": "", "b": "某次执行缺失"}],
                "a": a,
                "b": b,
                "source": source,
            }
        result = compare_executions(a, b)
        result["a"] = a
        result["b"] = b
        result["source"] = source
        return result
    except Exception as e:  # noqa: BLE001
        _log.warning("[idempotency] compare_case_runs 异常: %s", e)
        return {
            "identical": False,
            "diff": [{"field": "exception", "a": "", "b": str(e)[:200]}],
            "a": None,
            "b": None,
            "source": "exception",
        }


def summarize_idempotency(results: list[dict]) -> str:
    """生成报告文本（一致率 / 不一致明细），供 Step5 区块与脚本打印。"""
    if not results:
        return "幂等比对：无数据"
    identical = sum(1 for r in results if r.get("identical"))
    total = len(results)
    rate = (identical / total * 100) if total else 0
    lines = [
        "## 幂等性比对",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        "| 比对用例数 | %d |" % total,
        "| 一致 | %d |" % identical,
        "| 一致率 | %.1f%% |" % rate,
    ]
    mismatch = [r for r in results if not r.get("identical")]
    if mismatch:
        lines += ["", "### 不一致明细", ""]
        for r in mismatch[:20]:
            case = str(r.get("case_id", "") or "?")
            src = str(r.get("source", "") or "")
            diffs = "; ".join(
                "%s: %s≠%s" % (d.get("field"), d.get("a", ""), d.get("b", ""))
                for d in (r.get("diff") or [])[:5]
            )
            lines.append("- **%s** (%s): %s" % (case, src, diffs))
    return "\n".join(lines)
