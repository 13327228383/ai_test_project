# -*- coding: utf-8 -*-
"""Step5 报告生成 — 算法规则元信息清单（只读，供"算法规则清单"展示）。

Step5 的报告/审计逻辑散落在 _bug_report / _triage / bug_ticket / minimal_repro /
_perf_gate_report / mutation_guard / fake_pass_audit / idempotency / _a11y_report /
_manual_retest 等模块，均为规则驱动函数而非统一类注册表。此处集中暴露这些真实
存在的命名规则，保持与 Step2/Step3/Step4 的元信息结构一致（rule_id/name/
description/stage/priority/version）。

纯函数、无副作用、可独立单测。
"""

from __future__ import annotations

from typing import Any

# ──────────────────────────────────────────────
# Step5 算法规则清单
# ──────────────────────────────────────────────
_RULES: list[dict[str, Any]] = [
    {
        "rule_id": "step5.severity",
        "name": "缺陷严重度分级",
        "description": "规则驱动 severity 分级：high=数据破坏/安全/核心流程"
        "(insert/update/delete/写库/注入/越权/登录/支付/创建/提交)，low=文案/样式/"
        "拼写/显示，medium=性能/参数校验/边界/状态/并发，默认 medium。可解释、命中关键词即可"
        "定位规则。实现: _bug_report.classify_severity。",
        "stage": "bug",
        "priority": 90,
        "version": "1.0",
    },
    {
        "rule_id": "step5.triage",
        "name": "失败三分类与根因下钻",
        "description": "每个 Bug 增加 failure_triage：category∈{ENGINE引擎/断言|SUT被测"
        "系统|ENV环境数据|UNKNOWN fail-open}，root_cause_chain≥3 层根因链(表层→中间层→"
        "深层归属)且每层带 evidence_ref，纯增量不改流水线契约、不硬造根因。"
        "实现: _triage。",
        "stage": "bug",
        "priority": 85,
        "version": "1.0",
    },
    {
        "rule_id": "step5.minimal_repro",
        "name": "最小复现路径收敛",
        "description": "每个 Bug 增加 minimal_repro：preconditions/key_steps(≤3)/trigger/"
        "assertion/convergence_ratio，压缩复现步骤至最简关键路径，纯增量字段。"
        "实现: minimal_repro.converge_repro_strings。",
        "stage": "bug",
        "priority": 80,
        "version": "1.0",
    },
    {
        "rule_id": "step5.bug_ticket",
        "name": "结构化可提单缺陷单",
        "description": "build_bug_report(..., with_tickets=True) 额外产出 bug_tickets："
        "title/severity/repro_steps/expected/actual/root_cause/env/evidence_refs + "
        "priority/impact/minimal_repro/auto_fileable，可直接提缺陷单。"
        "实现: bug_ticket.BugTicket。",
        "stage": "bug",
        "priority": 76,
        "version": "1.0",
    },
    {
        "rule_id": "step5.perf_gate",
        "name": "性能门禁",
        "description": "基于 perf_baseline 基线对本轮执行做性能门禁判定(跨 run 对比)，"
        "超阈值触发门禁报告。实现: _perf_gate_report / perf_baseline。",
        "stage": "audit",
        "priority": 66,
        "version": "1.0",
    },
    {
        "rule_id": "step5.a11y_report",
        "name": "可访问性报告聚合",
        "description": "从 oracle_results(accessibility) 聚合本轮 a11y 明细为报告"
        "(覆盖/违规/严重度分布/用例级明细/违规样本)。实现: _a11y_report。",
        "stage": "audit",
        "priority": 62,
        "version": "1.0",
    },
    {
        "rule_id": "step5.fake_pass_audit",
        "name": "假 PASS 审计",
        "description": "审计 executions 中 PASS 用例是否取证完整(expected/actual/"
        "details/evidence)，发现无证据通过即标假 PASS，杜绝假绿。"
        "实现: fake_pass_audit。",
        "stage": "audit",
        "priority": 58,
        "version": "1.0",
    },
    {
        "rule_id": "step5.idempotency",
        "name": "报告幂等性",
        "description": "同一 run 重复生成报告保证输出幂等（不重复落库、不重复追加区块）。"
        "实现: idempotency。",
        "stage": "audit",
        "priority": 54,
        "version": "1.0",
    },
    {
        "rule_id": "step5.manual_retest",
        "name": "人工补测建议清单",
        "description": "依成功率/置信度/失败归因生成人工补测建议清单，指明需人工复核的"
        "点。实现: _manual_retest（Step5 后置人工审核闸门）。",
        "stage": "human",
        "priority": 50,
        "version": "1.0",
    },
    {
        "rule_id": "step5.attribution",
        "name": "失败归因",
        "description": "对失败用例按 PRODUCT_BUG/HARNESS_ERROR/ENVIRONMENT_ERROR/"
        "DATA_ERROR 等分类归因，支撑失败三分类与诊断中心。实现: attribution。",
        "stage": "bug",
        "priority": 72,
        "version": "1.0",
    },
]


def get_rule_meta_list() -> list[dict[str, Any]]:
    """获取 Step5 全部算法规则元信息清单。"""
    return list(_RULES)


__all__ = ["get_rule_meta_list"]