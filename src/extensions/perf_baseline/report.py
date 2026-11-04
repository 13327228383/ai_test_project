# -*- coding: utf-8 -*-
"""性能门禁报告 — 结构化 JSON + Markdown 可读摘要。

风格对齐 ``step5/_bug_report.py``（时间 ``isoformat(timespec="seconds")``）与
``step5/_report_sections.render_perf_section``（表格式 Markdown）。

本模块**只读渲染**：不做任何判定（判定唯一在 ``gate.evaluate_gate``），
不做任何基线写入（写入唯一在 ``baseline.BaselineManager.update``）。

对外函数全部不 raise。

[已复核-DeepSeek-V3 2026-08-16]
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from src.extensions.perf_baseline.schemas import (
    GATE_BASELINE_ESTABLISHED,
    GATE_FAIL,
    GATE_NO_DATA,
    GATE_PASS,
    GATE_PRE_GATE,
    GATE_STATUSES,
    PERCENTILE_ALGO,
    GateVerdict,
    PerfSample,
    now_ts,
)

_log = logging.getLogger(__name__)

#: 仓库根目录（src/extensions/perf_baseline/report.py → 上溯 3 层）
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

#: 默认报告输出目录
DEFAULT_REPORT_DIR = os.path.join(_REPO_ROOT, "data", "perf_reports")

#: 数据来源标识（写入报告，便于与 step5/perf_baseline.py 的 MySQL 链路区分）
DATA_SOURCE = "step4:PerfCollectorPlugin -> PerfContext"

#: Markdown 明细表最多渲染行数（防止报告过长）
_MAX_ROWS = 30

#: 终态 → 展示徽标
_STATUS_BADGE = {
    GATE_PASS: "✅ PASS",
    GATE_PRE_GATE: "⚠️ PRE_GATE",
    GATE_FAIL: "❌ FAIL",
    GATE_BASELINE_ESTABLISHED: "🆕 基线建立",
    GATE_NO_DATA: "🚫 NO_DATA",
}

_RE_UNSAFE = re.compile(r"[^0-9A-Za-z_\-.]")


def build_perf_report(
    target: str,
    run_id: str,
    verdicts: list[GateVerdict],
    samples: list[PerfSample],
    baseline_state: dict | None = None,
) -> dict[str, Any]:
    """构造性能门禁报告（§8.5 schema）。

    参数：
        target: 靶子名。
        run_id: 本轮运行 id。
        verdicts: :func:`gate.evaluate_gate` 的逐条结果。
        samples: 本轮全部采集样本（用于统计采集面）。
        baseline_state: ``{"version": int, "new_keys": [...],
            "entries_count": int, "path": str}``，缺省按空基线处理。

    返回：
        报告 dict。任何异常都被吞掉并返回结构完整的降级报告。
    """
    try:
        from src.extensions.perf_baseline.gate import summarize

        safe_verdicts = [v for v in (verdicts or []) if isinstance(v, GateVerdict)]
        state = baseline_state if isinstance(baseline_state, dict) else {}

        distribution = {status: 0 for status in GATE_STATUSES}
        for verdict in safe_verdicts:
            if verdict.status in distribution:
                distribution[verdict.status] += 1

        safe_samples = [s for s in (samples or []) if isinstance(s, PerfSample)]
        no_baseline_keys = [
            v.key for v in safe_verdicts if v.status == GATE_BASELINE_ESTABLISHED
        ]

        return {
            "generated_at": now_ts(),
            "data_source": DATA_SOURCE,
            "target": target or "unknown",
            "run_id": run_id or "",
            "baseline_version": int(state.get("version", 0) or 0),
            "baseline_path": str(state.get("path", "") or ""),
            "baseline_entries": int(state.get("entries_count", 0) or 0),
            # 整改 B2：分位数算法版本，供消费方判断跨报告可比性。
            "percentile_algo": PERCENTILE_ALGO,
            "gate_summary": summarize(safe_verdicts),
            "status_distribution": distribution,
            "sample_summary": {
                "collected": len(safe_samples),
                "valid": sum(1 for s in safe_samples if s.is_valid()),
                "failed": sum(1 for s in safe_samples if s.error),
                "web": sum(1 for s in safe_samples if s.kind == "web"),
                "api": sum(1 for s in safe_samples if s.kind == "api"),
            },
            "items": [v.to_dict() for v in safe_verdicts],
            "no_baseline_keys": no_baseline_keys,
        }
    except Exception as exc:  # noqa: BLE001 — 对外不 raise
        _log.warning("[perf_gate] 构造报告失败(降级): %s", str(exc)[:160])
        return {
            "generated_at": now_ts(),
            "data_source": DATA_SOURCE,
            "target": target or "unknown",
            "run_id": run_id or "",
            "baseline_version": 0,
            "baseline_path": "",
            "baseline_entries": 0,
            "gate_summary": {
                "total": 0, "pass": 0, "pre_gate": 0, "fail": 0,
                "baseline_established": 0, "no_data": 0, "blocking": False,
            },
            "status_distribution": {status: 0 for status in GATE_STATUSES},
            "sample_summary": {"collected": 0, "valid": 0, "failed": 0, "web": 0, "api": 0},
            "items": [],
            "no_baseline_keys": [],
            "error": str(exc)[:200],
        }


def render_perf_markdown(report: dict) -> str:
    """渲染 Markdown 摘要：门禁总览 + 违规明细 + 新建基线清单。

    参数：
        report: :func:`build_perf_report` 的返回值。

    返回：
        Markdown 文本；``report`` 为空或无任何判定项时返回 ``""``。
    """
    if not isinstance(report, dict) or not report:
        return ""
    try:
        summary = report.get("gate_summary") or {}
        items = report.get("items") or []
        if not items and not summary.get("total"):
            return ""

        blocking = bool(summary.get("blocking"))
        lines: list[str] = [
            "## 性能门禁（基线对比）",
            "",
            "> 数据来源：%s ｜ 靶子：`%s` ｜ 运行：`%s` ｜ 基线版本：v%s ｜ 生成时间：%s"
            % (
                report.get("data_source", "-"),
                report.get("target", "-"),
                report.get("run_id", "-"),
                report.get("baseline_version", 0),
                report.get("generated_at", "-"),
            ),
            "",
            "**总体结论：%s**" % ("❌ 存在阻断级性能退化" if blocking else "✅ 无阻断级性能退化"),
            "",
            "### 门禁总览",
            "",
            "| 判定项总数 | PASS | PRE_GATE | FAIL | 基线建立 | NO_DATA | 是否阻断 |",
            "|-----------|------|----------|------|---------|---------|---------|",
            "| %s | %s | %s | %s | %s | %s | %s |"
            % (
                summary.get("total", 0),
                summary.get("pass", 0),
                summary.get("pre_gate", 0),
                summary.get("fail", 0),
                summary.get("baseline_established", 0),
                summary.get("no_data", 0),
                "是" if blocking else "否",
            ),
        ]

        sample_summary = report.get("sample_summary") or {}
        if sample_summary:
            lines += [
                "",
                "> 采集面：共 %s 条样本（有效 %s / 失败 %s ｜ web %s / api %s）"
                % (
                    sample_summary.get("collected", 0),
                    sample_summary.get("valid", 0),
                    sample_summary.get("failed", 0),
                    sample_summary.get("web", 0),
                    sample_summary.get("api", 0),
                ),
            ]

        lines += _render_violation_table(items)
        lines += _render_no_data_table(items)
        lines += _render_new_baseline_list(report.get("no_baseline_keys") or [])
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        _log.warning("[perf_gate] 渲染 Markdown 失败(降级为空): %s", str(exc)[:160])
        return ""


def _render_violation_table(items: list) -> list[str]:
    """违规明细表（FAIL + PRE_GATE）。"""
    offenders = [
        item
        for item in items
        if isinstance(item, dict) and item.get("status") in (GATE_FAIL, GATE_PRE_GATE)
    ]
    if not offenders:
        return ["", "### 违规明细", "", "无违规项。"]

    # FAIL 优先、其次 PRE_GATE；组内按 delta_pct 降序
    def _sort_key(item: dict) -> tuple:
        rank = 0 if item.get("status") == GATE_FAIL else 1
        worst = max(
            (float(v.get("delta_pct", 0) or 0) for v in item.get("violations") or []),
            default=0.0,
        )
        return (rank, -worst)

    offenders.sort(key=_sort_key)

    lines = [
        "",
        "### 违规明细",
        "",
        "| 判定 | Key | 指标 | 当前值 | 基线均值 | 上浮% | 规则 | 阈值 | 说明 |",
        "|------|-----|------|-------|---------|------|------|------|------|",
    ]
    rows = 0
    for item in offenders:
        for violation in item.get("violations") or []:
            if rows >= _MAX_ROWS:
                lines.append("| ... | 其余违规项已省略 | - | - | - | - | - | - | 详见 JSON 报告 |")
                return lines
            lines.append(
                "| %s | `%s` | %s | %s | %s | %s | %s | %s | %s |"
                % (
                    _STATUS_BADGE.get(violation.get("level", ""), violation.get("level", "-")),
                    str(item.get("key", ""))[:56],
                    violation.get("metric", "-"),
                    violation.get("current", "-"),
                    violation.get("baseline", "-") or "-",
                    violation.get("delta_pct", "-"),
                    "绝对" if violation.get("rule_type") == "absolute" else "相对",
                    violation.get("threshold", "-"),
                    str(violation.get("reason", ""))[:60],
                )
            )
            rows += 1
    return lines


def _render_no_data_table(items: list) -> list[str]:
    """NO_DATA 明细（fail-closed：必须显式列出原因，不得静默）。"""
    blanks = [
        item
        for item in items
        if isinstance(item, dict) and item.get("status") == GATE_NO_DATA
    ]
    if not blanks:
        return []
    lines = [
        "",
        "### 无数据项（NO_DATA — 不计入 PASS）",
        "",
        "| Key | 类型 | 原因 |",
        "|-----|------|------|",
    ]
    for item in blanks[:_MAX_ROWS]:
        reasons = item.get("reasons") or []
        lines.append(
            "| `%s` | %s | %s |"
            % (
                str(item.get("key", ""))[:56] or "(未知)",
                item.get("kind", "-"),
                str(reasons[0] if reasons else "未知原因")[:80],
            )
        )
    if len(blanks) > _MAX_ROWS:
        lines.append("| ... | - | 其余 %d 项详见 JSON 报告 |" % (len(blanks) - _MAX_ROWS))
    return lines


def _render_new_baseline_list(keys: list) -> list[str]:
    """新建基线清单（首次见到的 key，本轮不做相对判定）。"""
    if not keys:
        return []
    lines = ["", "### 本轮新建基线（首次采集，未做相对判定）", ""]
    for key in keys[:_MAX_ROWS]:
        lines.append("- `%s`" % str(key)[:90])
    if len(keys) > _MAX_ROWS:
        lines.append("- ...（共 %d 项，详见 JSON 报告）" % len(keys))
    return lines


def save_json(report: dict, out_dir: str | None = None) -> str:
    """把报告写为 ``<out_dir>/perf_report_<run_id>.json``。

    返回：
        成功时返回文件绝对路径；失败返回 ``""``（不 raise）。
    """
    if not isinstance(report, dict) or not report:
        return ""
    directory = out_dir or DEFAULT_REPORT_DIR
    run_id = _RE_UNSAFE.sub("_", str(report.get("run_id") or "")) or "unknown"
    path = os.path.join(directory, f"perf_report_{run_id}.json")
    tmp_path = f"{path}.tmp.{os.getpid()}"
    try:
        os.makedirs(directory, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as fp:
            json.dump(report, fp, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        _log.info("[perf_gate] 报告已保存: %s", path)
        return os.path.abspath(path)
    except Exception as exc:  # noqa: BLE001
        _log.warning("[perf_gate] 报告保存失败(不阻断): %s", str(exc)[:160])
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError as clean_exc:
            # 残留 tmp 文件不影响正确性（下次 save 会覆盖），仅记录
            _log.warning(
                "[perf_gate] 清理临时报告文件失败: %s (%s)", tmp_path, str(clean_exc)[:100]
            )
        return ""


__all__ = [
    "build_perf_report",
    "render_perf_markdown",
    "save_json",
    "DEFAULT_REPORT_DIR",
    "DATA_SOURCE",
]
