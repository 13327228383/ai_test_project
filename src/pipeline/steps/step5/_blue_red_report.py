# -*- coding: utf-8 -*-
"""
Step 5 报告生成 — 蓝红对抗引擎
===============================
Blue Team: 决策者/技术详情 双视角并行生成报告
Red Team:  证据一致性 + 遗漏检查（结构化，不调 AI）
Arbiter:   综合双视角，修正矛盾，输出最终报告

★ 零硬编码：所有模型从 config 读取。
★ 解耦设计：不修改 report_generator.py，作为增强层独立存在。
★ v2 优化：executive + trend 合并为 decision 视角，3→2 AI 调用，省 1 次。
"""

import json
import logging
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

_log = logging.getLogger(__name__)


def _read_pipeline_config(key: str, default):
    """读取 pipeline_config 配置值（惰性导入，避免模块加载期循环依赖）。

    ★ F2 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）配套：seed 统一
      读 pipeline_config.ai_stability_seed，不硬编码 42。
    """
    try:
        from src.core.pipeline_config import _read_config_value as _cfg

        return _cfg(key, default)
    except Exception:
        return default


# ═══════════════════════════════════════════════════════════════
# 蓝队双视角 System Prompts（v2: executive+trend 合并为 decision）
# ═══════════════════════════════════════════════════════════════

_PERSPECTIVE_DECISION = (
    "你是资深测试经理兼数据分析师，当前视角是「决策者综合报告」。\n"
    "请根据以下测试数据生成面向管理层的综合报告，包含摘要、趋势与决策建议。\n\n"
    "要求：\n"
    "1. 用 3-5 句话总结整体测试结论\n"
    "2. 识别 P0 风险项（如有）\n"
    "3. 给出是否建议上线的明确判断\n"
    "4. 分析 pass_rate 变化趋势，识别新增失败 vs 历史遗留\n"
    "5. 预测质量风险并给出改进方向\n"
    "6. 数据驱动，每个结论都要引用具体数字\n\n"
    "输出严格 JSON：\n"
    '{"summary": "3-5句总结", "p0_risks": ["风险1", ...], '
    '"release_recommendation": "go|no_go|cautious", '
    '"key_metrics": {"pass_rate": 0.0, "critical_bugs": 0, "coverage": 0.0}, '
    '"trend_summary": "趋势总结", "pass_rate_trend": "up|down|stable", '
    '"new_failures": 0, "regression_failures": 0, '
    '"risk_forecast": "预测", "improvement_areas": ["方向1", ...]}'
)

_PERSPECTIVE_TECHNICAL = (
    "你是资深测试工程师，当前视角是「技术细节与根因分析」。\n"
    "请根据以下测试数据生成面向技术团队的报告。\n\n"
    "要求：\n"
    "1. 按模块/功能域分类统计 pass/fail\n"
    "2. 分析失败根因模式（如：UI定位失败/API超时/DB不一致）\n"
    "3. 识别 flaky 测试并分析可能原因\n"
    "4. 给出针对性修复建议\n\n"
    "输出严格 JSON：\n"
    '{"module_breakdown": [{"module":"", "pass":0, "fail":0, "pass_rate":0.0}], '
    '"failure_patterns": [{"pattern":"", "count":0, "root_cause":""}], '
    '"flaky_analysis": "分析", "fix_suggestions": ["建议1", ...]}'
)

PERSPECTIVES = {
    "decision": _PERSPECTIVE_DECISION,
    "technical": _PERSPECTIVE_TECHNICAL,
}

PERSPECTIVE_PARAMS = {
    "decision": {"temperature": 0.15, "label": "蓝队-决策者", "max_tokens": 1536},
    "technical": {"temperature": 0.15, "label": "蓝队-技术详情", "max_tokens": 1536},
}


# ═══════════════════════════════════════════════════════════════
# 辅助：构建测试数据摘要
# ═══════════════════════════════════════════════════════════════


def _build_data_summary(
    intents: list[dict],
    stats: dict[str, Any],
    catalog_stats: dict[str, dict],
    report_lines: list[str],
) -> str:
    """将报告数据压缩为 AI 友好的 JSON 摘要。"""
    total = stats.get("total", len(intents))
    passed = stats.get("passed", 0)
    failed = stats.get("failed", 0)
    skipped = stats.get("skipped", 0)
    pending = stats.get("pending", 0)
    pass_rate = passed / max(total - pending, 1) if total > pending else 0.0

    modules = []
    for cat, s in sorted(catalog_stats.items()):
        m_pass_rate = s["pass"] / max(s["total"], 1)
        modules.append(
            {
                "module": cat,
                "total": s["total"],
                "pass": s["pass"],
                "fail": s["fail"],
                "pass_rate": round(m_pass_rate, 3),
            }
        )

    return json.dumps(
        {
            "total_cases": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "pending": pending,
            "pass_rate": round(pass_rate, 3),
            "modules": modules[:20],
            "failed_intent_titles": [
                (i.get("case_id", "?") + ": " + (i.get("title") or "")[:60])
                for i in intents
                if i.get("_status") == "FAIL"
            ][:15],
        },
        ensure_ascii=False,
        indent=2,
    )


# ═══════════════════════════════════════════════════════════════
# 蓝队：单视角生成
# ═══════════════════════════════════════════════════════════════


def _generate_with_perspective(
    data_summary: str,
    perspective_name: str,
    system_prompt: str,
    ai_model: str,
) -> dict[str, Any]:
    """用指定视角生成报告片段。"""
    from src.ai.ai_client import ai_brain

    params = PERSPECTIVE_PARAMS.get(perspective_name, {})
    user_msg = "## 测试数据\n%s\n\n请从「%s」视角生成报告。" % (
        data_summary,
        params.get("label", perspective_name),
    )

    result = ai_brain._call_api(
        system_prompt=system_prompt,
        user_message=user_msg,
        temperature=params.get("temperature", 0.1),
        max_tokens=params.get("max_tokens", 1024),
        caller_name=params.get("label", "报告生成"),
        task_type="report_generation",
        cloud_model=ai_model,
        local_timeout=300,  # ★ 本地 7b 生成报告大 prompt 需 >60s，放宽到 300s
        # ★ F2 全局修复 [未复核-需 GLM/DeepSeek 把关]（2026-08-29）：
        #   仅加 seed=ai_stability_seed（temp 保持视角参数，不破坏双视角多样性）
        seed=(
            None
            if (_seed_v := _read_pipeline_config("ai_stability_seed", 42)) is None
            else int(_seed_v)
        ),
    )

    if not result.get("success"):
        return {"_error": "API调用失败: %s" % result.get("error", "?")[:120]}

    parsed = ai_brain._parse_json_response(result["data"])
    if parsed.get("success"):
        parsed["data"]["_perspective"] = perspective_name
        return parsed["data"]
    return {"_error": "JSON解析失败"}


# ═══════════════════════════════════════════════════════════════
# 红队：证据一致性 + 遗漏检查（结构化）
# ═══════════════════════════════════════════════════════════════


def _red_team_verify_report(
    stats: dict[str, Any],
    perspective_results: dict[str, dict],
    report_lines: list[str],
) -> dict[str, Any]:
    """红队验证报告质量（纯结构化分析，v2: 双视角）。

    检查：
    1. 证据一致性：双视角的关键指标是否一致（pass_rate/critical_bugs等）
    2. 遗漏检查：原始数据中有的模块/类型是否在报告中体现
    """
    issues = []

    # 1. 证据一致性：比较双视角的关键数字
    decision_data = perspective_results.get("decision", {})
    tech_data = perspective_results.get("technical", {})

    # 比较 pass_rate 一致性
    rates = []
    if isinstance(decision_data.get("key_metrics"), dict):
        rates.append(("决策者", decision_data["key_metrics"].get("pass_rate", 0)))
    if (
        isinstance(tech_data.get("module_breakdown"), list)
        and tech_data["module_breakdown"]
    ):
        avg = sum(m.get("pass_rate", 0) for m in tech_data["module_breakdown"]) / len(
            tech_data["module_breakdown"]
        )
        rates.append(("技术详情", avg))

    if len(rates) >= 2:
        diff = abs(rates[0][1] - rates[1][1])
        if diff > 0.15:
            issues.append(
                {
                    "type": "inconsistency",
                    "detail": "pass_rate 不一致: %s=%.1f%% vs %s=%.1f%% (diff=%.1f%%)"
                    % (
                        rates[0][0],
                        rates[0][1] * 100,
                        rates[1][0],
                        rates[1][1] * 100,
                        diff * 100,
                    ),
                }
            )

    # 2. 决策者视角的 pass_rate_trend 与实际数据对比
    actual_pass_rate = stats.get("pass_rate_value", 0)
    if (
        actual_pass_rate < 0.5
        and "up" in str(decision_data.get("pass_rate_trend", "")).lower()
    ):
        issues.append(
            {
                "type": "optimistic_trend",
                "detail": "实际 pass_rate=%.1f%% 但趋势报告判为 'up'，疑似过度乐观"
                % (actual_pass_rate * 100),
            }
        )

    # 3. 遗漏检查
    failed_titles = [i.get("title", "") for i in stats.get("failed_intents", [])]
    tech_failure_patterns = tech_data.get("failure_patterns", [])
    if failed_titles and not tech_failure_patterns:
        issues.append(
            {
                "type": "missing_analysis",
                "detail": "有 %d 个失败意图但技术报告无 failure_patterns 分析"
                % len(failed_titles),
            }
        )

    consistency_score = max(0.0, 1.0 - len(issues) * 0.2)
    verdict = (
        "consistent"
        if consistency_score >= 0.8
        else "minor_issues" if consistency_score >= 0.6 else "inconsistent"
    )

    return {
        "consistency_score": round(consistency_score, 3),
        "verdict": verdict,
        "issues": issues,
    }


# ═══════════════════════════════════════════════════════════════
# 公开入口
# ═══════════════════════════════════════════════════════════════


def generate_blue_red_report(
    intents: list[dict],
    stats: dict[str, Any],
    catalog_stats: dict[str, dict],
    report_lines: list[str],
    max_workers: int = 2,
) -> dict[str, Any]:
    """
    Step 5 蓝红对抗报告生成（v2: 双视角）。

    参数:
        intents:        测试用例列表
        stats:          统计数据 {total, passed, failed, skipped, pending, pass_rate_value, failed_intents}
        catalog_stats:  目录分布统计
        report_lines:   原始报告行（供红队参考）
        max_workers:    并行视角数（默认 2）

    返回:
        {
          "success": True/False,
          "perspectives": {decision: {...}, technical: {...}},
          "red_team": {consistency_score, verdict, issues},
          "enhancements": {release_recommendation, risks, improvement_areas},
        }
    """
    from src.core.config_loader import config

    ai_model = config.ai_model

    if not intents or stats.get("total", 0) == 0:
        return {
            "success": False,
            "error": "无测试数据",
            "perspectives": {},
            "red_team": {},
        }

    data_summary = _build_data_summary(intents, stats, catalog_stats, report_lines)

    _log.info("[蓝红 Step5] 启动 %d 视角并行生成报告...", len(PERSPECTIVES))

    # ── Phase 1: 蓝队双视角并行 ──
    perspective_results: dict[str, dict] = {}
    perspective_errors: list[str] = []

    actual_workers = min(max_workers, len(PERSPECTIVES))
    with ThreadPoolExecutor(max_workers=actual_workers) as executor:
        futures = {}
        for persp_name, prompt in PERSPECTIVES.items():
            f = executor.submit(
                _generate_with_perspective,
                data_summary,
                persp_name,
                prompt,
                ai_model,
            )
            futures[f] = persp_name

        # ★ 通用修复(2026-07-26, §10.9): future.result() 和 as_completed 加超时保护。
        #   根因(5层why): Step5 永久阻塞 → future.result() 无限等待 → 线程不返回
        #   → AI 调用层超时不一定触发线程返回 → 无超时导致永久阻塞。
        #   修复: future.result(timeout=300) + as_completed(timeout=300)，超时后降级输出。
        #   通用性: 适用于所有 ThreadPoolExecutor + AI 调用的并行场景。
        _PERSPECTIVE_TIMEOUT = 300  # 5分钟/视角
        _pending_names = set(PERSPECTIVES.keys())
        try:
            for future in as_completed(futures, timeout=_PERSPECTIVE_TIMEOUT):
                persp_name = futures[future]
                _pending_names.discard(persp_name)
                try:
                    result = future.result(timeout=10)
                    if result.get("_error"):
                        _log.warning(
                            "[蓝红 Step5] 蓝队 %s 失败: %s",
                            persp_name,
                            result["_error"],
                        )
                        perspective_errors.append(persp_name)
                    perspective_results[persp_name] = result
                except Exception as e:
                    _log.warning(
                        "[蓝红 Step5] 蓝队 %s 异常: %s", persp_name, str(e)[:120]
                    )
                    perspective_errors.append(persp_name)
                    perspective_results[persp_name] = {"_error": str(e)[:200]}
        except TimeoutError:
            _log.warning(
                "[蓝红 Step5] 并行报告生成超时(%ds)，未完成视角: %s",
                _PERSPECTIVE_TIMEOUT,
                sorted(_pending_names),
            )
            for persp_name in sorted(_pending_names):
                perspective_errors.append(persp_name)
                perspective_results[persp_name] = {
                    "_error": "视角超时(%ds)" % _PERSPECTIVE_TIMEOUT
                }

    # ── Phase 2: 红队验证 ──
    red_team_report = _red_team_verify_report(stats, perspective_results, report_lines)

    # ── Phase 3: 汇总增强建议 ──
    enhancements = {
        "release_recommendation": None,
        "risks": [],
        "improvement_areas": [],
    }

    decision_data = perspective_results.get("decision", {})
    if not decision_data.get("_error"):
        enhancements["release_recommendation"] = decision_data.get(
            "release_recommendation"
        )
        enhancements["risks"] = decision_data.get("p0_risks", [])
        enhancements["improvement_areas"] = decision_data.get("improvement_areas", [])

    tech_data = perspective_results.get("technical", {})
    if not tech_data.get("_error"):
        tech_suggestions = tech_data.get("fix_suggestions", [])
        if tech_suggestions:
            enhancements["improvement_areas"].extend(tech_suggestions)

    _log.info(
        "[蓝红 Step5] 完成: %d 视角生成, consistency=%.2f",
        len([k for k, v in perspective_results.items() if not v.get("_error")]),
        red_team_report["consistency_score"],
    )

    return {
        "success": True,
        "perspectives": {
            k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
            for k, v in perspective_results.items()
        },
        "red_team": red_team_report,
        "enhancements": enhancements,
        "errors": perspective_errors,
    }
