# -*- coding: utf-8 -*-

"""

Intent Validator - 主入口：三层校验编排 (v3.0)

==============================================

v3.0 关键变化:

  - cross_check 升级为确定性硬校验（零 AI 调用，查真实 DB schema/YApi/Figma）

  - cross_check 的 error 级问题 → 阻断（编造的表名/API路径/action type）

  - 支持自动修正：发现不存在表名→建议最相似真实表名

"""


from .constants import REQUIRED_FIELDS

from .result import ValidationResult

from .rule_check import quick_rule_check

from .ai_check import ai_deep_check

from .cross_check import run_cross_checks


import logging

_log = logging.getLogger(__name__)


def validate_intents(
    intents: list[dict],
    req_description: str = "",
    module: str = "",
    deep_check: bool = True,
) -> ValidationResult:
    """

    对生成的测试用例列表进行三层校验。



    v3.0 三层:

      第1层(免费): quick_rule_check — 必填字段/类型/格式 纯代码校验

      第2层(便宜): ai_deep_check — Flash AI 逻辑矛盾+可执行性

      第3层(零AI): cross_check — API路径/DB表名列名/UI元素/枚举值/action type

                    确定性硬校验(查真实地面真相)



    参数:

        intents:  待校验的测试用例列表 [{case_id, actions, checks, ...}]

        req_description: 原始需求描述（供 AI 深度校验用）

        module:   需求模块名

        deep_check: 是否启用 AI 深度校验（默认 True）



    返回:

        ValidationResult（含 passed, errors, warnings, summary, fix_hints）

    """

    # ★ P1: 前置基准健康度检查

    baseline_health = None

    try:

        from src.extensions.quality.baseline_health import check_baseline_health

        baseline_health = check_baseline_health()

    except Exception as _e:

        _log.debug("check + health + import: %s", _e, exc_info=True)

    # ═══ 第1层：快速规则校验（纯代码，毫秒级）═══

    result = quick_rule_check(intents)

    # ═══ 第2层：AI 深度校验（Flash 模型，秒级，低成本）═══

    deep_issue_count = 0

    if deep_check and result.passed_intents > 0:

        if baseline_health and baseline_health.overall == "critical":

            result.warnings.append(
                {
                    "case_id": "SYSTEM",
                    "reason": (
                        "⚠️ 基准数据严重缺失（YApi/Figma/DB Schema均未加载），"
                        "Flash校验仅靠AI常识判断，可能产生误判。"
                        "建议先执行Step1加载数据源。"
                    ),
                    "severity": "warning",
                    "cross_check": "baseline",
                }
            )

        ok_intents = [
            c
            for c in intents
            if isinstance(c, dict) and all(c.get(f) for f in REQUIRED_FIELDS)
        ]

        if ok_intents:

            ai_result = ai_deep_check(ok_intents, req_description, module)

            for issue in ai_result.get("issues", []):

                sev = issue.get("severity", "warning")

                entry = {
                    "case_id": issue.get("case_id", "?"),
                    "reason": issue.get("reason", ""),
                    "severity": sev,
                }

                if sev == "error":

                    result.errors.append(entry)

                    result.failed_intents += 1

                else:

                    result.warnings.append(entry)

                deep_issue_count += 1

            if not ai_result.get("passed", True):

                result.passed = False

                result.summary += (
                    f" | AI 深度校验发现 {len(ai_result.get('issues',[]))} 个问题"
                )

    # ═══ 第3层：P0 确定性交叉校验（零 AI，查真实地面真相）═══

    cross_result = run_cross_checks(intents)

    # ★ v3.0: 交叉校验的 error 级问题直接阻断（编造表名/API路径 → 不可放过）

    if cross_result.errors:

        result.errors.extend(cross_result.errors)

        result.failed_intents += len(cross_result.errors)

        result.passed = False

        result.summary += (
            f" | 确定性校验阻断 {len(cross_result.errors)} 个编造/不存在项"
        )

        # ★ 收集修正建议（供调用方自动应用）

        fix_hints = []

        for e in cross_result.errors:

            hint = e.get("fix_hint")

            if hint:

                fix_hints.append(
                    {
                        "case_id": e.get("case_id"),
                        "reason": e.get("reason", "")[:120],
                        "fix_hint": hint,
                        "cross_check": e.get("cross_check", ""),
                        "bad_table": e.get("bad_table", ""),
                        "bad_column": e.get("bad_column", ""),
                    }
                )

        if fix_hints:

            result.cross_check_counts["fix_hints"] = fix_hints

    if cross_result.warnings:

        result.warnings.extend(cross_result.warnings)

        if not cross_result.errors:

            result.summary += f" | 交叉校验发现 {len(cross_result.warnings)} 个疑似问题"

    # ★ 交叉校验分类计数

    for key in ("api", "ui", "enum", "db_trace", "action"):

        result.cross_check_counts[key] = cross_result.cross_check_counts.get(key, 0)

    result.cross_check_counts["deep"] = deep_issue_count

    result.cross_check_counts["rule"] = len(
        [e for e in result.errors if not e.get("cross_check")]
    )

    # ★ 基准健康度记录

    if baseline_health:

        result.cross_check_counts["baseline_status"] = baseline_health.overall

        if not baseline_health.can_run_cross_check:

            result.warnings.append(
                {
                    "case_id": "SYSTEM",
                    "reason": "⚠️ 交叉校验基准缺失（YApi/Figma/DB枚举未加载），API/UI/DB/枚举确定性校验已跳过。建议先执行Step1。",
                    "severity": "warning",
                    "cross_check": "baseline",
                }
            )

    return result
