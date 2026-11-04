# -*- coding: utf-8 -*-
"""引擎健康治理（W4-B，P0，用户红线②）—— 纯读聚合 HARNESS_ERROR，零 DB 写。

聚合 failure_category='HARNESS_ERROR' 行 → 按 failure_chain pattern 聚类
→ [{pattern, count, sample_case_ids, likely_module}]；HARNESS_ERROR 率 >
ENGINE_HEALTH_RED_RATE（默认 0.10）→ 报告标 RED + 告警（不阻断执行）。

likely_module 为纯启发式粗分（playwright_engine / locator / scroll / visual /
timeout / network / other），准确性需多 run 真实 failure_chain 调优（诚实标注）。

零 DDL：只读 executions 现有列（failure_category/failure_chain/failure_reason/case_id）。
"""
from __future__ import annotations
from src.core.guarded import guard

import json
import logging
from typing import Any

from src.core.pipeline_config import _read_config_value as _cfg

_log = logging.getLogger(__name__)

# 关键词 → likely_module（纯启发式；pattern 归一化后小写匹配）
_MODULE_KEYWORDS = (
    ("playwright_engine", ("playwright", "browser", "page.goto", "context")),
    ("locator", ("locator", "定位", "selector", "xpath")),
    ("scroll", ("scroll", "滚动")),
    ("visual", ("visual", "截图", "screenshot", "image")),
    ("timeout", ("timeout", "超时", "timed out")),
    ("network", ("net::err", "network", "网络", "connection")),
)


def _chain_pattern(failure_chain: str, failure_reason: str) -> str:
    """failure_chain JSON → 归一化 pattern（取最后策略名+detail 前 40 字符；
    无 chain 时取 failure_reason 前 40 字符；纯函数）。"""
    if failure_chain:
        try:
            steps = (
                json.loads(failure_chain)
                if isinstance(failure_chain, str)
                else failure_chain
            )
            if isinstance(steps, list) and steps:
                last = steps[-1]
                if isinstance(last, dict):
                    strat = str(last.get("strategy", "") or "").strip()
                    detail = str(last.get("detail", "") or "").strip()
                    return (strat + "|" + detail)[:40]
        except (ValueError, TypeError) as e:
            _log.warning("解析failure_chain失败: %s", e)
    return str(failure_reason or "").strip()[:40] or "other"


def _likely_module(pattern: str) -> str:
    """关键词 → likely_module（纯启发式，粗分）。"""
    p = str(pattern or "").lower()
    for module, keywords in _MODULE_KEYWORDS:
        if any(k in p for k in keywords):
            return module
    return "other"


def engine_health_report(project: str, run_id: str = "") -> dict[str, Any]:
    """聚合 failure_category='HARNESS_ERROR' 行 → 按 pattern 聚类。

    参数:
        project: 项目名（必填）。
        run_id: 按 run_id 过滤（默认空=全量）。

    返回:
        {project, total, harness_error, harness_rate, threshold, verdict("GREEN"/"RED"),
         patterns: [{pattern, count, sample_case_ids, likely_module}]}
    """
    project = str(project or "").strip()
    report: dict[str, Any] = {
        "project": project,
        "total": 0,
        "harness_error": 0,
        "harness_rate": 0.0,
        "threshold": 0.10,
        "verdict": "GREEN",
        "patterns": [],
    }
    try:
        report["threshold"] = float(_cfg("ENGINE_HEALTH_RED_RATE", 0.10) or 0.10)
    except (TypeError, ValueError):
        report["threshold"] = 0.10
    if not project:
        return report
    with guard("engine_health_report: from src.core.db_manager import get_", log=_log, level=logging.WARNING):
        from src.core.db_manager import get_connection

        conn = None
        try:
            conn = get_connection()
            if run_id:
                total_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM executions WHERE project=%s AND run_id=%s",
                    (project, run_id),
                ).fetchone()
                he_rows = (
                    conn.execute(
                        "SELECT failure_chain, failure_reason, case_id FROM executions "
                        "WHERE project=%s AND run_id=%s AND failure_category=%s",
                        (project, run_id, "HARNESS_ERROR"),
                    ).fetchall()
                    or []
                )
            else:
                total_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM executions WHERE project=%s",
                    (project,),
                ).fetchone()
                he_rows = (
                    conn.execute(
                        "SELECT failure_chain, failure_reason, case_id FROM executions "
                        "WHERE project=%s AND failure_category=%s",
                        (project, "HARNESS_ERROR"),
                    ).fetchall()
                    or []
                )
            report["total"] = int((total_row or {}).get("cnt", 0) or 0)
            cluster: dict[str, dict] = {}
            for r in he_rows:
                pattern = _chain_pattern(
                    r.get("failure_chain"), r.get("failure_reason")
                )
                item = cluster.setdefault(
                    pattern,
                    {
                        "pattern": pattern,
                        "count": 0,
                        "sample_case_ids": [],
                        "likely_module": "other",
                    },
                )
                item["count"] += 1
                cid = str(r.get("case_id", "") or "").strip()
                if (
                    cid
                    and cid not in item["sample_case_ids"]
                    and len(item["sample_case_ids"]) < 5
                ):
                    item["sample_case_ids"].append(cid)
                item["likely_module"] = _likely_module(pattern)
            report["harness_error"] = len(he_rows)
            total = report["total"]
            report["harness_rate"] = (report["harness_error"] / total) if total else 0.0
            report["verdict"] = (
                "RED" if report["harness_rate"] > report["threshold"] else "GREEN"
            )
            report["patterns"] = sorted(cluster.values(), key=lambda x: -x["count"])[
                :10
            ]
        finally:
            if conn:
                with guard("engine_health_report: conn.close()", log=_log, level=logging.WARNING):
                    conn.close()
    return report


def summarize_engine_health(report: dict) -> str:
    """报告文本：HARNESS_ERROR 率 + RED 告警（率>阈值默认 10%）+ 聚类 Top10。"""
    if not report:
        return ""
    total = int(report.get("total", 0) or 0)
    if total <= 0:
        return ""
    he = int(report.get("harness_error", 0) or 0)
    rate = float(report.get("harness_rate", 0.0) or 0.0)
    threshold = float(report.get("threshold", 0.10) or 0.10)
    verdict = str(report.get("verdict", "GREEN"))
    lines = [
        "## 引擎健康（HARNESS_ERROR 治理）",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        "| 执行总数 | %d |" % total,
        "| HARNESS_ERROR | %d |" % he,
        "| HARNESS_ERROR 率 | %.2f%% |" % (rate * 100),
        "| RED 阈值 | %.2f%% |" % (threshold * 100),
        "| 判定 | %s |" % ("🔴 RED（超阈值）" if verdict == "RED" else "🟢 GREEN"),
    ]
    if verdict == "RED":
        lines.append("")
        lines.append(
            "🔴 **HARNESS_ERROR 率超过阈值（%.2f%% > %.2f%%）**，引擎自身问题偏高，建议治理。"
            % (rate * 100, threshold * 100)
        )
    patterns = report.get("patterns") or []
    if patterns:
        lines += [
            "",
            "### 聚类 Top%d" % len(patterns),
            "",
            "| pattern | count | likely_module | sample_case_ids |",
            "|---------|-------|--------------|-----------------|",
        ]
        for p in patterns:
            lines.append(
                "| %s | %d | %s | %s |"
                % (
                    str(p.get("pattern", ""))[:30],
                    p.get("count", 0),
                    p.get("likely_module", "other"),
                    ",".join(p.get("sample_case_ids", []) or []),
                )
            )
    return "\n".join(lines)
