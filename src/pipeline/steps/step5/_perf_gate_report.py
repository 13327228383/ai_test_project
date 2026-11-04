# -*- coding: utf-8 -*-
"""性能门禁 step5 适配层（薄编排，零业务逻辑）。

编排链路::

    perf_context.drain(run_id)
        → load_rules(target)
        → BaselineManager(target)
        → evaluate_gate(sample, baseline_entry, rules)  ★ 判定唯一入口
        → summarize(verdicts)
        → BaselineManager.update(samples)               ★ 基线写入唯一入口
        → build_perf_report / save_json / render_perf_markdown

⚠️ 与本目录下的 ``perf_baseline.py`` **是两条独立链路**：
  - ``perf_baseline.py``：MySQL ``oracle_results`` → 质量面板（历史链路，不动）
  - 本模块：step4 采集 → PerfContext → 规则门禁（新链路，独立报告区块）

整体 try-except：任何异常只 warning，**绝不阻断报告生成**。

[已复核-DeepSeek-V3 2026-08-16]
"""

from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

_log = logging.getLogger(__name__)


def _resolve_run_id(target: str, run_id: str) -> str:
    """推导 run_id，**必须与 step4 ``PerfCollectorPlugin._resolve_run_id`` 一致**。

    ``generate_report`` 的 ``run_id`` 参数可能为 ``None``（多数调用方不传），
    此时回落到 ``versioning.current_run_id(target)`` —— 该函数由
    ``project + active version_label`` 确定性推导，与采集侧同 session 必然同值。
    取不到则用 ``"default"``，与采集侧兜底值保持一致。
    """
    candidate = str(run_id or "").strip()
    if candidate:
        return candidate
    with guard("_resolve_run_id: from src.core.versioning import curr", log=_log):
        from src.core.versioning import current_run_id

        candidate = str(current_run_id(target) or "").strip()
        if candidate:
            return candidate
    return "default"


def run_perf_gate(project: str, run_id: str) -> dict[str, Any]:
    """执行一次完整的性能门禁流程并返回报告 dict。

    参数：
        project: 靶子 / 项目名。
        run_id: 本轮运行 id（用于从 ``PerfContext`` 取样本）。

    返回：
        报告 dict（§8.5 schema）。无采集数据时返回 ``{}``。
        **永不抛异常**。
    """
    try:
        from src.extensions.perf_baseline.baseline import BaselineManager, write_after_gate
        from src.extensions.perf_baseline.config import load_rules, metrics_of
        from src.extensions.perf_baseline.context import perf_context
        from src.extensions.perf_baseline.gate import evaluate_gate
        from src.extensions.perf_baseline.report import build_perf_report, save_json

        target = (project or "").strip() or "unknown"
        run_id = _resolve_run_id(target, run_id)
        samples = perf_context.drain(run_id)
        if not samples:
            _log.warning(
                "[perf_gate] run=%s 无采集样本，跳过门禁（检查 perf_collector 插件是否启用）",
                run_id,
            )
            return {}

        rules = load_rules(target)
        if not rules.get("enabled", True):
            _log.info("[perf_gate] 靶子 %s 规则中 enabled=false，跳过门禁", target)
            return {}

        # 关停的指标组（如 api_demo 的 web 组）直接过滤，避免制造 NO_DATA 噪声
        active_kinds = {
            kind for kind in ("web", "api") if metrics_of(rules, kind)
        }
        evaluable = [s for s in samples if s.kind in active_kinds]
        skipped = len(samples) - len(evaluable)
        if skipped:
            _log.info("[perf_gate] %d 条样本因指标组关停被跳过", skipped)

        manager = BaselineManager(target)
        verdicts = [
            evaluate_gate(sample, manager.get_entry(sample.key), rules)
            for sample in evaluable
        ]

        baseline_state_before = {
            "version": manager.version(),
            "entries_count": len(manager.entries()),
            "path": manager.path,
        }
        # 基线回写必须在判定之后（否则本轮样本会污染自己的对比基准），
        #   且策略与靶子链路同源 —— 唯一入口 write_after_gate，本处只调用不重推。
        calibration = write_after_gate(
            manager, [s for s in evaluable if s.is_valid()], {}, run_id or ""
        )
        baseline_state = {
            "version": manager.version(),
            "entries_count": len(manager.entries()),
            "path": manager.path,
        }

        report = build_perf_report(
            target=target,
            run_id=run_id or "",
            verdicts=verdicts,
            samples=samples,
            baseline_state=baseline_state,
        )
        report["baseline_version_before"] = baseline_state_before["version"]
        report["calibration"] = calibration
        report["skipped_disabled_kind"] = skipped
        report["json_path"] = save_json(report)

        summary = report.get("gate_summary") or {}
        _log.info(
            "[perf_gate] target=%s run=%s → total=%s pass=%s pre_gate=%s fail=%s "
            "baseline_established=%s no_data=%s blocking=%s",
            target,
            run_id,
            summary.get("total"),
            summary.get("pass"),
            summary.get("pre_gate"),
            summary.get("fail"),
            summary.get("baseline_established"),
            summary.get("no_data"),
            summary.get("blocking"),
        )
        return report
    except Exception as exc:  # noqa: BLE001 — 报告链路绝不阻断
        _log.warning("[perf_gate] 门禁执行失败(不阻断报告): %s", str(exc)[:200])
        return {}


def build_perf_gate_section(project: str, run_id: str) -> str:
    """供 ``report_generator`` 调用：返回性能门禁 Markdown 区块。

    返回：
        Markdown 文本；无数据或异常时返回 ``""``（调用方据此不渲染区块）。
    """
    try:
        from src.extensions.perf_baseline.report import render_perf_markdown

        report = run_perf_gate(project, run_id)
        if not report:
            return ""
        return render_perf_markdown(report)
    except Exception as exc:  # noqa: BLE001
        _log.warning("[perf_gate] 区块渲染失败(不阻断): %s", str(exc)[:160])
        return ""


__all__ = ["run_perf_gate", "build_perf_gate_section"]
