# -*- coding: utf-8 -*-
"""性能回归对比 — 跨运行门禁结果对比，检测性能退化与改进。

职责：
  1. 加载历史报告（data/perf_reports/*.json）
  2. 对比当前运行 vs 上一轮运行的门禁判定
  3. 检测：新 FAIL、新 PRE_GATE、指标退化/改进、基线变化
  4. 产出结构化回归对比报告

与 gate.py 的关系：
  - gate.py 做**单轮门禁判定**（绝对阈值 + 相对基线）
  - 本模块做**跨轮回归检测**（本轮 vs 前轮，状态漂移）

设计原则：
  - 纯函数 + 无 IO（除文件加载），可被单测 100% 覆盖
  - 对外函数不 raise：异常内部 catch → warning → 安全默认值
  - 通用：不认任何靶子业务逻辑，只认 key / status / metrics

[已复核-DeepSeek-V3 2026-08-24]
"""

from __future__ import annotations
from src.core.guarded import guard

import json
import logging
import os
from typing import Any

from src.extensions.perf_baseline.schemas import (
    GATE_BASELINE_ESTABLISHED,
    GATE_FAIL,
    GATE_NO_DATA,
    GATE_PASS,
    GATE_PRE_GATE,
    GATE_STATUSES,
    now_ts,
)

_log = logging.getLogger(__name__)

#: 仓库根目录
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

#: 历史报告目录
DEFAULT_REPORT_DIR = os.path.join(_REPO_ROOT, "data", "perf_reports")

#: 状态 → 严重度（用于排序：FAIL > PRE_GATE > NO_DATA > BASELINE_ESTABLISHED > PASS）
_STATUS_SEVERITY = {
    GATE_FAIL: 4,
    GATE_PRE_GATE: 3,
    GATE_NO_DATA: 2,
    GATE_BASELINE_ESTABLISHED: 1,
    GATE_PASS: 0,
}

#: 回归方向
REGRESSION_NEW_FAIL = "new_fail"
REGRESSION_NEW_PRE_GATE = "new_pre_gate"
REGRESSION_DEGRADED = "degraded"
REGRESSION_IMPROVED = "improved"
REGRESSION_RESOLVED = "resolved"
REGRESSION_SAME = "same"
REGRESSION_NEW_KEY = "new_key"
REGRESSION_REMOVED_KEY = "removed_key"


def _load_report(path: str) -> dict[str, Any] | None:
    """加载单个报告文件。损坏/不存在返回 None。"""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as exc:
        _log.warning("[perf_regression] 报告加载失败(%s): %s", path, str(exc)[:120])
        return None


def load_history(
    report_dir: str | None = None,
    target: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """加载历史性能报告，按时间倒序排列。

    参数：
        report_dir: 报告目录，缺省 ``data/perf_reports``。
        target: 过滤靶子名，None 表示不过滤。
        limit: 最多加载条数。

    返回：
        报告 dict 列表，按 ``generated_at`` 倒序。
    """
    directory = report_dir or DEFAULT_REPORT_DIR
    if not os.path.isdir(directory):
        return []

    reports: list[dict[str, Any]] = []
    with guard("load_history: for fname in sorted(os.listdir(direc", log=_log, level=logging.WARNING):
        for fname in sorted(os.listdir(directory), reverse=True):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(directory, fname)
            report = _load_report(path)
            if not report:
                continue
            if target and report.get("target", "") != target:
                continue
            reports.append(report)
            if len(reports) >= limit:
                break

    # 按时间倒序
    reports.sort(key=lambda r: r.get("generated_at", ""), reverse=True)
    return reports


def _build_verdict_index(items: list[dict]) -> dict[str, dict]:
    """构建 key → verdict 索引。"""
    index: dict[str, dict] = {}
    for item in items or []:
        key = item.get("key", "")
        if key:
            index[key] = item
    return index


def _compare_metric_values(
    prev_metrics: dict[str, float],
    curr_metrics: dict[str, float],
    degrade_pct: float = 20.0,
) -> list[dict[str, Any]]:
    """对比同一 key 的指标值，检测退化与改进。

    参数：
        prev_metrics: 前一轮指标值。
        curr_metrics: 当前轮指标值。
        degrade_pct: 退化阈值（%），当前值比前值高超过此百分比视为退化。

    返回：
        退化/改进明细列表。
    """
    changes: list[dict[str, Any]] = []
    all_metrics = set(prev_metrics.keys()) | set(curr_metrics.keys())

    for metric in sorted(all_metrics):
        prev_val = prev_metrics.get(metric)
        curr_val = curr_metrics.get(metric)

        if prev_val is None or curr_val is None:
            continue

        try:
            prev_f = float(prev_val)
            curr_f = float(curr_val)
        except (TypeError, ValueError):
            continue

        if prev_f == 0:
            if curr_f > 0:
                changes.append({
                    "metric": metric,
                    "direction": "degraded",
                    "previous": prev_f,
                    "current": curr_f,
                    "delta_pct": 100.0,  # 从 0 到正数视为 100% 退化
                    "reason": f"{metric}: {prev_f} → {curr_f}（新出现）",
                })
            continue

        delta_pct = round((curr_f - prev_f) / prev_f * 100.0, 2)

        if delta_pct >= degrade_pct:
            changes.append({
                "metric": metric,
                "direction": "degraded",
                "previous": round(prev_f, 2),
                "current": round(curr_f, 2),
                "delta_pct": delta_pct,
                "reason": f"{metric}: {round(prev_f, 2)} → {round(curr_f, 2)} (+{delta_pct}%)",
            })
        elif delta_pct <= -degrade_pct:
            changes.append({
                "metric": metric,
                "direction": "improved",
                "previous": round(prev_f, 2),
                "current": round(curr_f, 2),
                "delta_pct": delta_pct,
                "reason": f"{metric}: {round(prev_f, 2)} → {round(curr_f, 2)} ({delta_pct}%)",
            })

    return changes


def _classify_status_change(
    prev_status: str,
    curr_status: str,
    key: str,
    prev_verdict: dict | None,
    curr_verdict: dict | None,
    degrade_pct: float = 20.0,
) -> dict[str, Any]:
    """分类单个 key 的状态变化。

    返回：
        {key, previous_status, current_status, direction, severity, metrics_changes, details}
    """
    prev_sev = _STATUS_SEVERITY.get(prev_status, 0)
    curr_sev = _STATUS_SEVERITY.get(curr_status, 0)

    result: dict[str, Any] = {
        "key": key,
        "previous_status": prev_status,
        "current_status": curr_status,
        "direction": REGRESSION_SAME,
        "severity": "none",
        "metrics_changes": [],
        "details": "",
    }

    # 状态变化分类
    if curr_sev > prev_sev:
        # 退化
        if curr_status == GATE_FAIL:
            result["direction"] = REGRESSION_NEW_FAIL
            result["severity"] = "critical"
            result["details"] = f"前轮 {prev_status} → 本轮 FAIL"
        elif curr_status == GATE_PRE_GATE:
            result["direction"] = REGRESSION_NEW_PRE_GATE
            result["severity"] = "warning"
            result["details"] = f"前轮 {prev_status} → 本轮 PRE_GATE"
        elif prev_status == GATE_PASS and curr_status == GATE_NO_DATA:
            result["direction"] = REGRESSION_DEGRADED
            result["severity"] = "warning"
            result["details"] = "前轮 PASS → 本轮 NO_DATA（采集失败）"
        else:
            result["direction"] = REGRESSION_DEGRADED
            result["severity"] = "minor"
            result["details"] = f"前轮 {prev_status} → 本轮 {curr_status}"
    elif curr_sev < prev_sev:
        # 改进
        if prev_status == GATE_FAIL and curr_status == GATE_PASS:
            result["direction"] = REGRESSION_RESOLVED
            result["severity"] = "info"
            result["details"] = "前轮 FAIL → 本轮 PASS（已修复）"
        elif prev_status == GATE_FAIL and curr_status == GATE_PRE_GATE:
            result["direction"] = REGRESSION_IMPROVED
            result["severity"] = "info"
            result["details"] = "前轮 FAIL → 本轮 PRE_GATE（部分改善）"
        else:
            result["direction"] = REGRESSION_IMPROVED
            result["severity"] = "info"
            result["details"] = f"前轮 {prev_status} → 本轮 {curr_status}"
    else:
        # 同状态，但可以检测指标级变化
        result["direction"] = REGRESSION_SAME
        result["severity"] = "none"

    # 指标级变化检测（即使状态相同，指标也可能退化）
    if prev_verdict and curr_verdict:
        prev_metrics = prev_verdict.get("metrics", {})
        curr_metrics = curr_verdict.get("metrics", {})
        if isinstance(prev_metrics, dict) and isinstance(curr_metrics, dict):
            result["metrics_changes"] = _compare_metric_values(
                prev_metrics, curr_metrics, degrade_pct
            )

    return result


def compare(
    current_report: dict[str, Any],
    previous_report: dict[str, Any],
    degrade_pct: float = 20.0,
) -> dict[str, Any]:
    """对比两轮性能报告，产出回归检测结果。

    参数：
        current_report: 本轮报告（build_perf_report 产物）。
        previous_report: 前一轮报告。
        degrade_pct: 指标退化阈值（%），默认 20%。

    返回：
        回归对比报告 dict。
    """
    try:
        curr_items = current_report.get("items", []) if isinstance(current_report, dict) else []
        prev_items = previous_report.get("items", []) if isinstance(previous_report, dict) else []

        curr_index = _build_verdict_index(curr_items)
        prev_index = _build_verdict_index(prev_items)

        all_keys = set(curr_index.keys()) | set(prev_index.keys())

        changes: list[dict[str, Any]] = []
        new_keys: list[str] = []
        removed_keys: list[str] = []
        critical_count = 0
        warning_count = 0
        degraded_count = 0
        improved_count = 0

        for key in sorted(all_keys):
            curr_verdict = curr_index.get(key)
            prev_verdict = prev_index.get(key)

            if curr_verdict and not prev_verdict:
                new_keys.append(key)
                continue
            if prev_verdict and not curr_verdict:
                removed_keys.append(key)
                continue

            prev_status = prev_verdict.get("status", GATE_NO_DATA) if prev_verdict else GATE_NO_DATA
            curr_status = curr_verdict.get("status", GATE_NO_DATA) if curr_verdict else GATE_NO_DATA

            change = _classify_status_change(
                prev_status, curr_status, key, prev_verdict, curr_verdict, degrade_pct
            )
            changes.append(change)

            if change["severity"] == "critical":
                critical_count += 1
            elif change["severity"] == "warning":
                warning_count += 1

            if change["direction"] == REGRESSION_DEGRADED:
                degraded_count += 1
            elif change["direction"] in (REGRESSION_IMPROVED, REGRESSION_RESOLVED):
                improved_count += 1

        # 汇总
        curr_summary = current_report.get("gate_summary", {}) if isinstance(current_report, dict) else {}
        prev_summary = previous_report.get("gate_summary", {}) if isinstance(previous_report, dict) else {}

        return {
            "generated_at": now_ts(),
            "current_run": {
                "run_id": current_report.get("run_id", "") if isinstance(current_report, dict) else "",
                "generated_at": current_report.get("generated_at", "") if isinstance(current_report, dict) else "",
                "target": current_report.get("target", "") if isinstance(current_report, dict) else "",
                "summary": curr_summary,
            },
            "previous_run": {
                "run_id": previous_report.get("run_id", "") if isinstance(previous_report, dict) else "",
                "generated_at": previous_report.get("generated_at", "") if isinstance(previous_report, dict) else "",
                "target": previous_report.get("target", "") if isinstance(previous_report, dict) else "",
                "summary": prev_summary,
            },
            "regression_summary": {
                "total_keys_compared": len(changes),
                "new_keys": len(new_keys),
                "removed_keys": len(removed_keys),
                "critical": critical_count,
                "warning": warning_count,
                "degraded_metrics": degraded_count,
                "improved": improved_count,
                "has_regression": critical_count > 0 or warning_count > 0,
            },
            "changes": changes,
            "new_keys": new_keys,
            "removed_keys": removed_keys,
            "degrade_pct": degrade_pct,
        }
    except Exception as exc:
        _log.error("[perf_regression] 对比失败: %s", exc, exc_info=True)
        return {
            "generated_at": now_ts(),
            "error": str(exc)[:200],
            "regression_summary": {"has_regression": False, "total_keys_compared": 0},
        }


def compare_with_history(
    current_report: dict[str, Any],
    report_dir: str | None = None,
    target: str | None = None,
    degrade_pct: float = 20.0,
) -> dict[str, Any]:
    """对比当前报告与历史最近一轮报告。

    参数：
        current_report: 本轮报告。
        report_dir: 报告目录。
        target: 靶子名过滤。
        degrade_pct: 退化阈值。

    返回：
        回归对比报告。无历史报告时返回空对比。
    """
    history = load_history(report_dir, target, limit=1)
    if not history:
        return {
            "generated_at": now_ts(),
            "info": "无历史报告可对比",
            "regression_summary": {"has_regression": False, "total_keys_compared": 0},
        }

    previous = history[0]
    # 跳过与自己同 run_id 的报告
    if isinstance(current_report, dict) and previous.get("run_id") == current_report.get("run_id"):
        if len(history) > 1:
            previous = history[1]
        else:
            return {
                "generated_at": now_ts(),
                "info": "历史报告与当前为同一 run_id，无可对比的上一轮",
                "regression_summary": {"has_regression": False, "total_keys_compared": 0},
            }

    return compare(current_report, previous, degrade_pct)


def render_regression_markdown(regression_report: dict[str, Any]) -> str:
    """将回归对比报告渲染为 Markdown 摘要。

    参数：
        regression_report: ``compare()`` 或 ``compare_with_history()`` 的返回值。

    返回：
        Markdown 字符串。
    """
    if regression_report.get("error"):
        return f"**性能回归对比失败**: {regression_report['error']}"

    if regression_report.get("info"):
        return f"**性能回归对比**: {regression_report['info']}"

    lines: list[str] = []
    summary = regression_report.get("regression_summary", {})
    current = regression_report.get("current_run", {})
    previous = regression_report.get("previous_run", {})

    lines.append("## 性能回归对比")
    lines.append("")
    lines.append(f"| 维度 | 当前 ({current.get('run_id', '?')}) | 前轮 ({previous.get('run_id', '?')}) |")
    lines.append("|------|------|------|")

    curr_sum = current.get("summary", {})
    prev_sum = previous.get("summary", {})

    for status in GATE_STATUSES:
        label = {
            GATE_PASS: "PASS",
            GATE_PRE_GATE: "PRE_GATE",
            GATE_FAIL: "FAIL",
            GATE_BASELINE_ESTABLISHED: "基线建立",
            GATE_NO_DATA: "NO_DATA",
        }.get(status, status)
        curr_val = curr_sum.get(status.lower(), 0)
        prev_val = prev_sum.get(status.lower(), 0)
        lines.append(f"| {label} | {curr_val} | {prev_val} |")

    lines.append(f"| 阻断 | {'是' if curr_sum.get('blocking') else '否'} | {'是' if prev_sum.get('blocking') else '否'} |")
    lines.append("")

    # 回归摘要
    lines.append(f"**回归检测**: 关键 {summary.get('critical', 0)} | 告警 {summary.get('warning', 0)} | 新增 key {summary.get('new_keys', 0)} | 移除 key {summary.get('removed_keys', 0)}")
    lines.append("")

    # 详细变化
    changes = regression_report.get("changes", [])
    critical_changes = [c for c in changes if c.get("severity") == "critical"]
    warning_changes = [c for c in changes if c.get("severity") == "warning"]
    improved_changes = [c for c in changes if c.get("direction") in (REGRESSION_IMPROVED, REGRESSION_RESOLVED)]
    degraded_metric_changes = [c for c in changes if c.get("metrics_changes")]

    if critical_changes:
        lines.append("### 关键回归 (new FAIL)")
        lines.append("")
        for c in critical_changes:
            lines.append(f"- **{c['key']}**: {c.get('details', '')}")
        lines.append("")

    if warning_changes:
        lines.append("### 告警 (new PRE_GATE / 采集失败)")
        lines.append("")
        for c in warning_changes:
            lines.append(f"- **{c['key']}**: {c.get('details', '')}")
        lines.append("")

    if improved_changes:
        lines.append("### 改善")
        lines.append("")
        for c in improved_changes:
            lines.append(f"- **{c['key']}**: {c.get('details', '')}")
        lines.append("")

    if degraded_metric_changes:
        lines.append("### 指标级变化")
        lines.append("")
        for c in degraded_metric_changes:
            for mc in c.get("metrics_changes", []):
                lines.append(f"- **{c['key']}**: {mc.get('reason', '')}")
        lines.append("")

    new_keys = regression_report.get("new_keys", [])
    removed_keys = regression_report.get("removed_keys", [])

    if new_keys:
        lines.append(f"### 新增 key ({len(new_keys)})")
        lines.append("")
        for k in new_keys[:10]:
            lines.append(f"- `{k}`")
        if len(new_keys) > 10:
            lines.append(f"- ... 及其他 {len(new_keys) - 10} 个")
        lines.append("")

    if removed_keys:
        lines.append(f"### 移除 key ({len(removed_keys)})")
        lines.append("")
        for k in removed_keys[:10]:
            lines.append(f"- `{k}`")
        if len(removed_keys) > 10:
            lines.append(f"- ... 及其他 {len(removed_keys) - 10} 个")
        lines.append("")

    return "\n".join(lines)


def save_regression_report(
    regression_report: dict[str, Any],
    output_dir: str | None = None,
    run_id: str = "",
) -> str:
    """保存回归对比报告为 JSON 文件。

    返回：
        保存的文件路径。失败返回空字符串。
    """
    directory = output_dir or DEFAULT_REPORT_DIR
    try:
        os.makedirs(directory, exist_ok=True)
        safe_id = (run_id or "regression").replace(" ", "_").replace(":", "-")
        fname = f"regression_{safe_id}.json"
        path = os.path.join(directory, fname)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(regression_report, fp, ensure_ascii=False, indent=2)
        _log.info("[perf_regression] 回归报告已保存: %s", path)
        return path
    except Exception as exc:
        _log.warning("[perf_regression] 回归报告保存失败: %s", str(exc)[:120])
        return ""


__all__ = [
    "DEFAULT_REPORT_DIR",
    "compare",
    "compare_with_history",
    "load_history",
    "render_regression_markdown",
    "save_regression_report",
    "REGRESSION_NEW_FAIL",
    "REGRESSION_NEW_PRE_GATE",
    "REGRESSION_DEGRADED",
    "REGRESSION_IMPROVED",
    "REGRESSION_RESOLVED",
]