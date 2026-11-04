# -*- coding: utf-8 -*-
"""人工补测建议清单生成器（v3.0）

交叉比对 requirements + test_cases + executions 三表，
找出未覆盖/失败/Flaky 的需求和用例，按风险分级输出人工补测建议。

契约: §4.1 "规则优先" — 风险分级用纯规则，不调 AI
"""

import logging
from src.core.guarded import guard
from typing import Any

_log = logging.getLogger(__name__)

# 风险等级排序/取值域（与用例 risk_level 词表一致）
_RISK_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def generate_manual_retest_section(
    project_name: str,
    run_id: str,
    intents: list[dict],
    reqs: list[dict],
    failed_intents: list[dict],
) -> dict[str, Any]:
    """生成「人工补测建议清单」Markdown 章节。

    参数:
        project_name: 项目名
        run_id:       流水线运行ID
        intents:      全部测试用例（用于覆盖率计算）
        reqs:         全部需求（用于找未覆盖需求）
        failed_intents: 失败用例列表（复用主函数已计算的 failed_intents）

    返回:
        {"success": bool, "markdown": str, "suggestions": list[dict]}
    """
    try:
        suggestions = _collect_retest_suggestions(
            project_name, run_id, intents, reqs, failed_intents
        )
        # ★ H4-4: 记录人工补测建议指标
        with guard("generate_manual_retest_section: from src.extensions.metrics_collecto", log=_log):
            from src.extensions.metrics_collector import metrics_collector
            from collections import Counter as _Counter

            _type_counts = _Counter(s.get("type", "") for s in suggestions)
            _risk_counts = _Counter(s.get("risk", "") for s in suggestions)
            metrics_collector.record_manual_retest(
                project_name,
                run_id,
                uncovered_reqs=_type_counts.get("未覆盖需求", 0),
                failed_cases=_type_counts.get("失败用例", 0),
                flaky_cases=_type_counts.get("Flaky用例", 0),
                undecidable_cases=_type_counts.get("未能判定用例", 0),
                risk_counts=dict(_risk_counts),
            )
        markdown = _format_retest_section(suggestions)
        return {
            "success": True,
            "markdown": markdown,
            "suggestions": suggestions,
        }
    except Exception as e:
        _log.warning("[ManualRetest] 生成失败: %s", str(e)[:120])
        return {"success": False, "markdown": "", "suggestions": []}


def _collect_retest_suggestions(
    project_name: str,
    run_id: str,
    intents: list[dict],
    reqs: list[dict],
    failed_intents: list[dict],
) -> list[dict[str, Any]]:
    """收集所有需要人工补测的建议项。"""
    suggestions = []

    # 1. 未覆盖需求（无测试用例）
    suggestions.extend(_find_uncovered_reqs(reqs, intents))

    # 2. 失败用例（PRODUCT_BUG 需人工复现）
    suggestions.extend(_find_failed_cases(failed_intents))

    # 3. Flaky 用例（需人工复跑）
    suggestions.extend(_find_flaky_cases(intents))

    # 4. 未能判定用例（执行端 SKIP：运行期无机判定通道/导航失败等 → 必须人工验证）
    suggestions.extend(_find_skipped_cases(intents))

    # 按风险等级排序
    suggestions.sort(key=lambda s: _RISK_ORDER.get(s.get("risk", "P3"), 3))

    return suggestions


def _find_uncovered_reqs(reqs: list[dict], intents: list[dict]) -> list[dict]:
    """找出无测试用例的需求。"""
    covered_req_ids = set()
    for intent in intents:
        rid = str(intent.get("req_id", "") or "").strip()
        if rid:
            covered_req_ids.add(rid)

    suggestions = []
    for req in reqs:
        rid = str(req.get("req_id", "") or "").strip()
        if not rid:
            continue
        is_generated = req.get("is_generated", 0)
        if rid in covered_req_ids or is_generated:
            continue

        operation = str(req.get("operation", "") or "").lower()
        risk = _classify_req_risk(operation)

        suggestions.append(
            {
                "id": rid,
                "type": "未覆盖需求",
                "module": req.get("module", ""),
                "operation": req.get("operation", ""),
                "expected": req.get("expected_feedback", ""),
                "reason": "无任何测试用例覆盖",
                "risk": risk,
                "suggestion": "补正向用例（参照需求三元组操作+预期）",
            }
        )
    return suggestions


def _classify_req_risk(operation: str) -> str:
    """基于操作关键词的风险分级（纯规则，无 AI）。"""
    # P0：核心 CRUD 操作
    if any(
        k in operation
        for k in [
            "新增",
            "编辑",
            "删除",
            "创建",
            "修改",
            "提交",
            "create",
            "edit",
            "delete",
            "update",
            "submit",
        ]
    ):
        return "P0"
    # P1：查询/搜索/导出/审批
    if any(
        k in operation
        for k in [
            "查询",
            "搜索",
            "导出",
            "下载",
            "审批",
            "search",
            "filter",
            "export",
            "download",
        ]
    ):
        return "P1"
    # P2：展示/查看
    if any(
        k in operation
        for k in ["展示", "显示", "查看", "浏览", "view", "display", "show"]
    ):
        return "P2"
    return "P3"


def _find_failed_cases(failed_intents: list[dict]) -> list[dict]:
    """找出失败用例中需要人工补测的。"""
    suggestions = []
    for intent in failed_intents:
        case_id = str(intent.get("case_id", "") or "").strip()
        if not case_id:
            continue
        failure_category = str(intent.get("failure_category", "") or "").strip()
        risk_level = str(intent.get("risk_level", "") or "").strip()

        # HARNESS_ERROR 是执行系统问题，不需要人工补测
        if failure_category == "HARNESS_ERROR":
            continue

        if failure_category == "PRODUCT_BUG":
            risk = "P0" if ("高" in risk_level or "P0" in risk_level) else "P1"
        else:
            risk = "P2"

        suggestions.append(
            {
                "id": case_id,
                "type": "失败用例",
                "module": intent.get("module", ""),
                "operation": intent.get("title", ""),
                "expected": "",
                "reason": f"{failure_category}: {str(intent.get('_failure_reason', ''))[:80]}",
                "risk": risk,
                "suggestion": (
                    "人工复现并验证是否为系统bug"
                    if failure_category == "PRODUCT_BUG"
                    else "检查环境/数据问题"
                ),
            }
        )
    return suggestions


def _find_flaky_cases(intents: list[dict]) -> list[dict]:
    """找出 Flaky 用例（需人工复跑确认）。

    与 SKIP 同源：读 case_store 补齐的 verification_status，避免与报告主表
    状态各查一条路径（同一状态两处推导 → 报告内自相矛盾）。
    """
    suggestions = []
    for intent in intents:
        cid = str(intent.get("case_id", "") or "").strip()
        if not cid or str(intent.get("verification_status", "") or "").upper() != "FLAKY":
            continue
        suggestions.append(
            {
                "id": cid,
                "type": "Flaky用例",
                "module": intent.get("catalog_name", "") or intent.get("module", ""),
                "operation": intent.get("title", ""),
                "expected": "",
                "reason": f"Flaky: {str(intent.get('_failure_reason', '') or '')[:80]}",
                "risk": "P1",
                "suggestion": "人工复跑确认稳定性",
            }
        )
    return suggestions


def _find_skipped_cases(intents: list[dict]) -> list[dict]:
    """找出执行端判为 SKIP 的用例（未真正验证，必须人工补测）。

    判据唯一来源 = case_store 已补齐的 verification_status / failure_category
    （与报告主表同一份执行状态，按版本取最新执行），不另查 executions，也不在此
    重新推导「断言是否可判定」——同一语义多处推导必然漂移。
    """
    suggestions = []
    for intent in intents:
        cid = str(intent.get("case_id", "") or "").strip()
        if not cid or str(intent.get("verification_status", "") or "").upper() != "SKIP":
            continue
        risk = str(intent.get("risk_level", "") or "").strip().upper()
        if risk not in _RISK_ORDER:
            risk = "P2"
        category = str(intent.get("failure_category", "") or "").strip() or "SKIP"
        reason = str(intent.get("_failure_reason", "") or "").strip()
        suggestions.append(
            {
                "id": cid,
                "type": "未能判定用例",
                "module": intent.get("catalog_name", "") or intent.get("module", ""),
                "operation": intent.get("title", ""),
                "expected": "",
                "reason": ("%s: %s" % (category, reason))[:220],
                "risk": risk,
                "suggestion": "按需求三元组人工验证该期望是否成立，并补充可机判断言",
            }
        )
    return suggestions


def _format_retest_section(suggestions: list[dict]) -> str:
    """格式化为 Markdown 章节。"""
    if not suggestions:
        return "\n## 人工补测建议清单\n\n> 所有需求和用例均已覆盖，无需人工补测。\n"

    lines = ["\n## 人工补测建议清单\n"]
    lines.append("> 以下需求/用例建议人工补充测试，按风险分级排序。\n")

    for risk_level, risk_title in [
        ("P0", "阻塞级（必须人工补测）"),
        ("P1", "高优先级"),
        ("P2", "中优先级"),
        ("P3", "低优先级"),
    ]:
        group = [s for s in suggestions if s.get("risk") == risk_level]
        if not group:
            continue
        lines.append(f"### {risk_level} - {risk_title}（{len(group)} 条）\n")
        lines.append("| ID | 类型 | 模块 | 原因 | 建议操作 |")
        lines.append("|---|---|---|---|---|")
        for s in group:
            reason = s["reason"][:120].replace("|", "/")
            lines.append(
                f"| {s['id']} | {s['type']} | {s.get('module', '')} | {reason} | {s['suggestion']} |"
            )
        lines.append("")

    lines.append("### 汇总\n")
    lines.append("| 等级 | 数量 |")
    lines.append("|---|---|")
    for risk_level in ["P0", "P1", "P2", "P3"]:
        count = sum(1 for s in suggestions if s.get("risk") == risk_level)
        if count > 0:
            lines.append(f"| {risk_level} | {count} |")
    lines.append("")

    return "\n".join(lines)
