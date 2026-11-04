# -*- coding: utf-8 -*-
"""性能测试门禁 — step4 集成包装器（perf_gate 总入口）。

============================================================
架构设计（通用性能测试框架）
============================================================

★ 三层架构（已实现→待接入）：

  第1层 · 采集层（collector）     : src/extensions/perf_baseline/collector.py
      MetricCollector — Web（W3C Navigation/Resource Timing）+
      API（ApiTracker 调用记录归一化）→ PerfSample

  第2层 · 门禁层（gate）           : src/extensions/perf_baseline/gate.py
      evaluate_gate(PerfSample, baseline_entry, rules) → GateVerdict
      6 级优先级短路求值：NO_DATA → FAIL(绝对) → BASELINE_ESTABLISHED → FAIL(相对) → PRE_GATE → PASS

  第3层 · 集成层（本模块）         : 接入 step4 流水线，串联采集→门禁→报告
      浏览器模式：安装 MetricCollector → 每用例执行后采集 Web 指标 → 门禁判定
      API 模式：收集 ApiTracker 调用 → 归一化 API 指标 → 门禁判定

★ 配置驱动（perf_rules.yaml，唯一配置源）：
  - defaults: 全局默认阈值（ttfb_ms≤800, dcl_ms≤2500, load_ms≤5000, ...）
  - targets.<name>: 靶子专属覆盖（smartshop 放宽 resource_*, api_demo 关停 web 组）
  - relative: 相对基线上浮分界（pre_gate_pct=80%, fail_pct=100%）

★ 门禁语义（fail-closed，永不静默 PASS）：
  - FAIL: 绝对阈值超限 或 相对基线上浮≥100% → 阻断门禁
  - PRE_GATE: 相对基线上浮∈[80%,100%) → 只告警不阻断
  - BASELINE_ESTABLISHED: 首次采集该 key → 建立基线，本轮不做相对判定
  - NO_DATA: 采集失败 / 指标为空 / 规则未启用 → 暴露但不误判为 PASS
  - PASS: 全部指标在阈值内

★ 通用性保障：
  - 不认任何靶子业务逻辑，只认 kind / method / url
  - 靶子差异全部由 perf_rules.yaml 承担
  - 新增靶子只需改 yaml，无需改 Python

============================================================
接入方式
============================================================

  浏览器模式（_run_browser_mode）:
    1. MetricCollector.install(page) — 注入 PerformanceObserver（幂等）
    2. 每用例执行完成后 → MetricCollector.collect_web() → PerfSample
    3. 全部用例完成后 → 批量 evaluate_gate(samples) → GateVerdict[]
    4. summarize(verdicts) → 产出门禁汇总

  API 模式（_run_api_mode）:
    1. 收集 ApiTracker._recent_requests 调用记录
    2. MetricCollector.collect_api(calls) → PerfSample[]
    3. 批量 evaluate_gate(samples) → GateVerdict[]
    4. summarize(verdicts) → 产出门禁汇总

============================================================
报告 schema（新增 ``perf`` 顶层键）
============================================================

  {
    "perf": {
      "enabled": true,
      "samples": 3,                    // 采集样本数
      "verdicts": [                    // 逐个 key 的判定
        {
          "key": "web::/products",
          "kind": "web",
          "status": "PASS",
          "metrics": {"ttfb_ms": 45.2, "dcl_ms": 230.1, ...},
          "violations": [],
          "reasons": ["全部指标在基线与绝对阈值范围内"]
        }
      ],
      "summary": {                     // 门禁汇总
        "total": 3, "pass": 2, "pre_gate": 0, "fail": 0,
        "baseline_established": 1, "no_data": 0, "blocking": false
      }
    }
  }
"""

from __future__ import annotations
from src.core.guarded import guard

import logging
from typing import Any

_log = logging.getLogger(__name__)

# 靶子级 perf 配置默认值
DEFAULT_PERF_CONFIG: dict[str, Any] = {
    "enabled": True,          # 默认开启（可在 target_cfg 中关闭）
    "collect_web": True,      # 是否采集 Web 指标
    "collect_api": True,      # 是否采集 API 指标
}


def run_perf_check_browser(
    page: Any,
    target_cfg: dict,
    run_id: str = "",
    case_id: str = "",
) -> dict[str, Any]:
    """浏览器模式：采集当前页面 Web 性能指标。

    参数：
        page: Playwright Page 对象。
        target_cfg: 靶子配置 dict（含可选的 ``perf`` 段）。
        run_id: 本轮运行标识（可选，用于样本归属）。
        case_id: 用例标识（可选，用于样本归属）。

    返回：
        PerfSample dict（含 metrics/error），供门禁判定。
    """
    perf_cfg = {**DEFAULT_PERF_CONFIG, **(target_cfg.get("perf") or {})}
    if not perf_cfg.get("enabled", True) or not perf_cfg.get("collect_web", True):
        return {"enabled": False, "sample": None, "error": "perf 采集未启用"}

    try:
        from src.extensions.perf_baseline.collector import MetricCollector
        from src.extensions.perf_baseline.schemas import now_ts

        # 安装 Observer（幂等）
        MetricCollector.install(page)

        target = target_cfg.get("target_key", "") or target_cfg.get("url", "")
        sample = MetricCollector.collect_web(
            page=page,
            target=target,
            run_id=run_id or now_ts(),
            case_id=case_id or "",
        )
        return {
            "enabled": True,
            "sample": sample.to_dict() if sample else None,
            "error": sample.error if sample and sample.error else "",
        }
    except Exception as exc:
        _log.warning("[perf] Web 采集异常: %s", str(exc)[:120])
        return {
            "enabled": True,
            "sample": None,
            "error": f"Web 采集异常: {str(exc)[:160]}",
        }


def run_perf_check_api(
    api_calls: list[dict],
    target_cfg: dict,
    run_id: str = "",
    case_id: str = "",
) -> dict[str, Any]:
    """API 模式：从 ApiTracker 调用记录归一化 API 性能指标。

    参数：
        api_calls: ApiTracker._recent_requests 风格的 dict 列表。
        target_cfg: 靶子配置 dict。
        run_id: 本轮运行标识。
        case_id: 用例标识。

    返回：
        {samples: [PerfSample dict], error: str}。
    """
    perf_cfg = {**DEFAULT_PERF_CONFIG, **(target_cfg.get("perf") or {})}
    if not perf_cfg.get("enabled", True) or not perf_cfg.get("collect_api", True):
        return {"enabled": False, "samples": [], "error": "perf API 采集未启用"}

    try:
        from src.extensions.perf_baseline.collector import MetricCollector

        target = target_cfg.get("target_key", "") or target_cfg.get("url", "")
        samples = MetricCollector.collect_api(
            calls=api_calls,
            target=target,
            run_id=run_id,
            case_id=case_id or "",
        )
        return {
            "enabled": True,
            "samples": [s.to_dict() for s in samples] if samples else [],
            "error": "",
        }
    except Exception as exc:
        _log.warning("[perf] API 采集异常: %s", str(exc)[:120])
        return {
            "enabled": True,
            "samples": [],
            "error": f"API 采集异常: {str(exc)[:160]}",
        }


def evaluate_perf_gate(
    samples: list[dict],
    target_cfg: dict,
    run_id: str = "",
) -> dict[str, Any]:
    """对采集的样本批量运行门禁判定。

    参数：
        samples: PerfSample.to_dict() 列表。
        target_cfg: 靶子配置 dict。

    返回：
        {verdicts: [GateVerdict dict], summary: {total, pass, fail, ...}}。
    """
    perf_cfg = {**DEFAULT_PERF_CONFIG, **(target_cfg.get("perf") or {})}
    if not perf_cfg.get("enabled", True):
        return {"enabled": False, "verdicts": [], "summary": {}}

    if not samples:
        return {
            "enabled": True,
            "verdicts": [],
            "summary": {"total": 0, "reason": "无有效样本"},
            "calibration": {"mode": bool(perf_cfg.get("update_baseline", False)),
                            "status": "skipped_no_samples",
                            "reason": "无任何样本，基线未更新"},
        }

    try:
        from src.extensions.perf_baseline.schemas import PerfSample
        from src.extensions.perf_baseline.config import load_rules
        from src.extensions.perf_baseline.gate import evaluate_gate, summarize
        from src.extensions.perf_baseline.baseline import BaselineManager, write_after_gate

        target_key = target_cfg.get("target_key", "") or "unknown"
        rules = load_rules(target_key)
        bm = BaselineManager(target_key)
        baseline_data = bm.load()
        baseline_entries = baseline_data.get("entries", {}) if baseline_data else {}

        verdicts: list[dict] = []
        valid_samples: list[PerfSample] = []
        for sample_dict in samples:
            sample = PerfSample.from_dict(sample_dict)
            if not sample.is_valid():
                verdicts.append({
                    "key": sample.key or "unknown",
                    "kind": sample.kind,
                    "status": "NO_DATA",
                    "error": sample.error or "样本无效",
                })
                continue
            valid_samples.append(sample)

            key = sample.key
            baseline_entry = baseline_entries.get(key)
            verdict = evaluate_gate(sample, baseline_entry, rules)
            verdicts.append(verdict.to_dict())

        # 基线回写策略的唯一定义在 write_after_gate（首轮建立 / 常规只判定 / 校准才回写）；
        #   本处只调用，不重推策略 —— 否则同一语义会出现第二个判定入口并随运行漂移。
        calibration = write_after_gate(bm, valid_samples, perf_cfg, run_id)

        # 构造 GateVerdict 对象列表供 summarize
        _verdict_objs = []
        for v in verdicts:
            from src.extensions.perf_baseline.schemas import GateVerdict
            gv = GateVerdict(
                status=v.get("status", "NO_DATA"),
                target=v.get("target", ""),
                key=v.get("key", ""),
                kind=v.get("kind", "web"),
                metrics=v.get("metrics", {}),
            )
            _verdict_objs.append(gv)
        summary = summarize(_verdict_objs)
        return {
            "enabled": True,
            "samples": len(samples),
            "verdicts": verdicts,
            "summary": summary,
            "calibration": calibration,
            "error": "",
        }
    except Exception as exc:
        _log.error("[perf] 门禁判定异常: %s", exc, exc_info=True)
        return {
            "enabled": True,
            "samples": len(samples) if samples else 0,
            "verdicts": [],
            "summary": {"error": str(exc)[:160]},
            "error": f"门禁判定异常: {str(exc)[:160]}",
        }


def run_perf_regression_check(
    gate_result: dict[str, Any],
    target_cfg: dict,
    report_dir: str | None = None,
) -> dict[str, Any]:
    """在门禁判定后，与历史报告进行回归对比。

    参数：
        gate_result: ``evaluate_perf_gate`` 的返回值。
        target_cfg: 靶子配置 dict。
        report_dir: 历史报告目录，缺省 ``data/perf_reports``。

    返回：
        {enabled, regression_report, error}。
    """
    perf_cfg = {**DEFAULT_PERF_CONFIG, **(target_cfg.get("perf") or {})}
    if not perf_cfg.get("enabled", True):
        return {"enabled": False, "regression_report": None, "error": "perf 未启用"}

    if not gate_result.get("verdicts"):
        return {"enabled": True, "regression_report": None, "info": "无门禁结果可对比"}

    try:
        from src.extensions.perf_baseline.regression import compare_with_history

        target_key = target_cfg.get("target_key", "") or "unknown"
        # 构造当前报告（最小结构，与 build_perf_report 格式兼容）
        current_report = {
            "generated_at": "",
            "target": target_key,
            "run_id": gate_result.get("run_id", ""),
            "items": gate_result.get("verdicts", []),
            "gate_summary": gate_result.get("summary", {}),
        }
        regression = compare_with_history(
            current_report,
            report_dir=report_dir,
            target=target_key,
        )
        return {
            "enabled": True,
            "regression_report": regression,
            "error": regression.get("error", "") or regression.get("info", ""),
        }
    except Exception as exc:
        _log.error("[perf] 回归对比异常: %s", exc, exc_info=True)
        return {
            "enabled": True,
            "regression_report": None,
            "error": f"回归对比异常: {str(exc)[:160]}",
        }


def run_full_perf_check(
    page: Any,
    api_calls: list[dict] | None,
    target_cfg: dict,
    run_id: str = "",
    case_id: str = "",
    regression_report_dir: str | None = None,
) -> dict[str, Any]:
    """一站式性能检查门禁入口（采集 → 门禁 → 回归对比）。

    这是 step4 常规调用入口：一次调用完成 Web+API 采集 + 门禁判定 + 回归对比。

    参数：
        page: Playwright Page（browser 模式时传入；API 模式传 None）。
        api_calls: ApiTracker 调用记录（API 模式时传入；browser 模式传 None）。
        target_cfg: 靶子配置 dict。
        run_id / case_id: 归属信息。
        regression_report_dir: 回归对比历史报告目录。

    返回：
        {enabled, samples, verdicts, summary, regression, error} — 与 step4 报告 schema 兼容。
    """
    all_samples: list[dict] = []

    # Web 采集
    if page is not None:
        web_result = run_perf_check_browser(page, target_cfg, run_id, case_id)
        if web_result.get("enabled") and web_result.get("sample"):
            all_samples.append(web_result["sample"])

    # API 采集
    if api_calls:
        api_result = run_perf_check_api(api_calls, target_cfg, run_id, case_id)
        if api_result.get("enabled") and api_result.get("samples"):
            all_samples.extend(api_result["samples"])

    # 门禁判定
    gate_result = evaluate_perf_gate(all_samples, target_cfg, run_id=run_id)
    gate_result["run_id"] = run_id

    # 回归对比（与历史最近一轮对比）
    regression_result = run_perf_regression_check(
        gate_result, target_cfg, report_dir=regression_report_dir
    )

    return {
        "enabled": gate_result.get("enabled", True),
        "samples": gate_result.get("samples", len(all_samples)),
        "verdicts": gate_result.get("verdicts", []),
        "summary": gate_result.get("summary", {}),
        "calibration": gate_result.get("calibration"),
        "regression": regression_result.get("regression_report"),
        "error": gate_result.get("error", ""),
    }


async def run_full_perf_check_async(
    page: Any,
    api_calls: list[dict] | None,
    target_cfg: dict,
    run_id: str = "",
    case_id: str = "",
    regression_report_dir: str | None = None,
) -> dict[str, Any]:
    """一站式性能门禁入口 — **async** Page 专用（常规流水线/串行后端挂载用）。

    与 :func:`run_full_perf_check` 语义完全一致（采集→门禁→回归对比），唯一
    区别是 Web 采集走 ``MetricCollector.collect_web_async``（await ``evaluate``），
    通用适配 step4 的 async PlaywrightEngine。API 采集/门禁判定/回归对比三块
    与同步路径完全共用，不存在重复实现。

    参数：
        page: **async** Playwright Page（browser 模式时传入；API 模式传 None）。
        api_calls: ApiTracker 调用记录（API 模式时传入；browser 模式传 None）。
        target_cfg: 靶子配置 dict。
        run_id / case_id: 归属信息。
        regression_report_dir: 回归对比历史报告目录。

    返回：
        {enabled, samples, verdicts, summary, regression, error} — 与同步入口同构。
    """
    all_samples: list[dict] = []

    # Web 采集（async 路径）
    if page is not None:
        with guard("run_full_perf_check_async: from src.extensions.perf_baseline.sc", log=_log, level=logging.WARNING):
            # ★ 根因修复(2026-09-21): 本函数使用 now_ts() 但未导入（导入只存在于
            #   另一函数 :119 内）→ async 采集路径 100% 抛 NameError → 永远 0 样本、
            #   性能门禁在 async 后端恒空转。与同步路径同款函数内导入。
            from src.extensions.perf_baseline.schemas import now_ts

            from src.extensions.perf_baseline.collector import MetricCollector

            perf_cfg = {**DEFAULT_PERF_CONFIG, **(target_cfg.get("perf") or {})}
            if perf_cfg.get("enabled", True) and perf_cfg.get("collect_web", True):
                await MetricCollector.install_async(page)
                target = target_cfg.get("target_key", "") or target_cfg.get("url", "")
                sample = await MetricCollector.collect_web_async(
                    page=page,
                    target=target,
                    run_id=run_id or now_ts(),
                    case_id=case_id or "",
                )
                if sample.to_dict().get("metrics") or sample.error:
                    all_samples.append(sample.to_dict())
            elif not perf_cfg.get("collect_web", True):
                _log.debug("[perf] Web 采集未启用(async)，跳过")

    # API 采集（与同步路径共用 run_perf_check_api）
    if api_calls:
        api_result = run_perf_check_api(api_calls, target_cfg, run_id, case_id)
        if api_result.get("enabled") and api_result.get("samples"):
            all_samples.extend(api_result["samples"])

    # 门禁判定（与同步路径共用）
    gate_result = evaluate_perf_gate(all_samples, target_cfg, run_id=run_id)
    gate_result["run_id"] = run_id

    # 回归对比（与同步路径共用）
    regression_result = run_perf_regression_check(
        gate_result, target_cfg, report_dir=regression_report_dir
    )

    return {
        "enabled": gate_result.get("enabled", True),
        "samples": gate_result.get("samples", len(all_samples)),
        "verdicts": gate_result.get("verdicts", []),
        "summary": gate_result.get("summary", {}),
        "calibration": gate_result.get("calibration"),
        "regression": regression_result.get("regression_report"),
        "error": gate_result.get("error", ""),
    }