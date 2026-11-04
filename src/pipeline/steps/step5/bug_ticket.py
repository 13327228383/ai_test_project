# -*- coding: utf-8 -*-
"""结构化缺陷提单 schema（D4，L1→L3）—— 可直接推送到 issue 系统的 bug ticket。

背景（CAPABILITY_GAP_PRD）：
  - D4「缺陷报告可提单性」：缺陷单需含 环境/账号/步骤/预期/实际/证据/影响面/初判优先级，
    开发可直接接手。现状 Step5 有 severity 分级 + 复现步骤 + failure_triage 字段，
    但**未落实结构化提单 schema**（本模块补齐）。
  - Q7「失败分类准确率下限」：分类置信度 ≥ 0.80 才自动提单，低于则进人工复核队列。
  - D1「不把用例缺陷/环境问题当 bug 提给开发」：ENGINE/ENV/UNKNOWN 三类不自动提单。

本模块职责（纯数据结构 + 纯函数，零 DB 写、零 DDL、零网络）：
  - TicketEnv     : 环境信息（项目/URL/浏览器/DB/账号/引擎版本）
  - EvidenceRef   : 证据引用（截图/日志/DB 快照/执行记录）
  - BugTicket     : 缺陷单主体，字段覆盖 D4 全部要求
  - 序列化        : to_dict / to_json / to_markdown + from_dict / from_json（往返一致）
  - 装配          : build_ticket_from_bug（单条）/ build_tickets_from_report（批量）

字段与上游映射（全部来自 _bug_report.build_bug_report 的 bug dict，不新增采集）：
  title           ← bug.title（severity + case_id）
  severity        ← bug.severity（classify_severity 规则驱动）
  repro_steps     ← bug.repro_steps（_load_case_steps 回放）
  minimal_repro   ← minimal_repro.converge_repro_strings（★ D3 收敛结果）
  expected/actual ← bug.expected / bug.actual
  root_cause      ← bug.failure_triage.root_cause_chain（≥3 层根因链）
  env             ← 调用方传入 TicketEnv（缺省 fail-open 填 unknown，不臆造）
  evidence_refs   ← bug.screenshot + bug.failure_triage.evidence_refs + execution_id

[未复核-需 GLM/DeepSeek 把关]（新增模块，寇豆码 新增）
"""

from __future__ import annotations
from src.core.guarded import guard

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional

_log = logging.getLogger(__name__)

# ★ D3 收敛：把回放步骤收敛为 ≤3 步关键路径 + 前置条件
from src.pipeline.steps.step5.minimal_repro import (
    DEFAULT_MAX_KEY_STEPS,
    converge_repro_strings,
)

TICKET_SCHEMA_VERSION = "bug-ticket-v1"

# 自动提单置信度下限（CAPABILITY_GAP_PRD Q7：≥0.80 才自动提单）
AUTO_FILE_CONFIDENCE_MIN = 0.80

# 仅 SUT（被测系统缺陷）可自动提单给开发；
# ENGINE=引擎自身缺陷（内部修）/ ENV=环境问题（不是 bug）/ UNKNOWN=证据不足
AUTO_FILE_CATEGORIES: frozenset[str] = frozenset({"SUT"})

# severity → 初判优先级（issue 系统常用 P0~P3 口径）
_SEVERITY_TO_PRIORITY: dict[str, str] = {
    "high": "P1",
    "medium": "P2",
    "low": "P3",
}

# severity → 影响面文案（可解释，不臆造具体业务影响）
_SEVERITY_TO_IMPACT: dict[str, str] = {
    "high": "影响数据正确性/安全/核心业务流程，阻塞主干场景",
    "medium": "影响非核心功能或边界场景，主干流程可绕行",
    "low": "仅影响文案/样式/展示，不影响功能可用性",
}


# ═══════════════════════════════════════════════════════════════════════════
# 子结构：环境信息
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class TicketEnv:
    """缺陷单环境信息（D4 要求的「环境 + 账号」）。

    所有字段默认 "unknown"/"" —— fail-open，不臆造未知环境信息。
    """

    project: str = "unknown"
    base_url: str = "unknown"
    browser: str = "unknown"
    viewport: str = "unknown"
    db: str = "unknown"
    account: str = "unknown"
    engine_version: str = "unknown"
    executed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "TicketEnv":
        """从 dict 反序列化（未知键忽略，缺失键取默认值）。"""
        data = data or {}
        allowed = {f for f in cls.__dataclass_fields__}  # noqa: SLF001
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def to_markdown(self) -> str:
        """渲染为 Markdown 表格行块。"""
        rows = [
            ("项目", self.project),
            ("被测地址", self.base_url),
            ("浏览器", self.browser),
            ("分辨率", self.viewport),
            ("数据库", self.db),
            ("账号", self.account),
            ("引擎版本", self.engine_version),
            ("执行时间", self.executed_at or "unknown"),
        ]
        lines = ["| 项 | 值 |", "|---|---|"]
        lines.extend(f"| {k} | {v or 'unknown'} |" for k, v in rows)
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# 子结构：证据引用
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class EvidenceRef:
    """单条证据引用（D4 要求的「证据」）。

    Attributes:
        kind: 证据类型（screenshot / execution / triage_field / console / db / network）。
        ref: 证据定位串（文件路径、executions.id、字段名等）。
        note: 可读说明。
    """

    kind: str = "unknown"
    ref: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "EvidenceRef":
        """从 dict 反序列化。"""
        data = data or {}
        allowed = {f for f in cls.__dataclass_fields__}  # noqa: SLF001
        return cls(**{k: v for k, v in data.items() if k in allowed})


# ═══════════════════════════════════════════════════════════════════════════
# 主体：缺陷单
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class BugTicket:
    """结构化缺陷单 —— 可直接序列化推送到 issue 系统。

    D4 必需字段：title / severity / repro_steps / expected / actual /
    root_cause / env / evidence_refs（均为一等字段，不塞进 extra）。

    补充字段（开发可直接接手所需）：
        ticket_id / project / case_id / check_type / priority / impact /
        minimal_repro（★ D3 收敛清单）/ triage_category / triage_confidence /
        auto_fileable / file_blockers / labels / created_at / schema_version
    """

    # ── D4 必需字段 ──
    title: str = ""
    severity: str = "medium"
    repro_steps: list[str] = field(default_factory=list)
    expected: Optional[str] = None
    actual: Optional[str] = None
    root_cause: dict[str, Any] = field(default_factory=dict)
    env: TicketEnv = field(default_factory=TicketEnv)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)

    # ── 提单可消费性补充字段 ──
    ticket_id: str = ""
    project: str = ""
    case_id: str = ""
    check_type: str = ""
    priority: str = "P2"
    impact: str = ""
    minimal_repro: dict[str, Any] = field(default_factory=dict)
    triage_category: str = "UNKNOWN"
    triage_confidence: float = 0.0
    auto_fileable: bool = False
    file_blockers: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    created_at: str = ""
    schema_version: str = TICKET_SCHEMA_VERSION

    # ───────────────────────────────────────────────────────────────────
    # 序列化
    # ───────────────────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        """序列化为纯 dict（嵌套 dataclass 一并展开，可直接 json.dumps）。"""
        return {
            "schema_version": self.schema_version,
            "ticket_id": self.ticket_id,
            "title": self.title,
            "severity": self.severity,
            "priority": self.priority,
            "impact": self.impact,
            "project": self.project,
            "case_id": self.case_id,
            "check_type": self.check_type,
            "repro_steps": list(self.repro_steps),
            "minimal_repro": dict(self.minimal_repro),
            "expected": self.expected,
            "actual": self.actual,
            "root_cause": dict(self.root_cause),
            "env": self.env.to_dict(),
            "evidence_refs": [e.to_dict() for e in self.evidence_refs],
            "triage_category": self.triage_category,
            "triage_confidence": self.triage_confidence,
            "auto_fileable": self.auto_fileable,
            "file_blockers": list(self.file_blockers),
            "labels": list(self.labels),
            "created_at": self.created_at,
        }

    def to_json(self, indent: Optional[int] = 2) -> str:
        """序列化为 JSON 字符串（中文不转义）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BugTicket":
        """从 dict 反序列化（与 :meth:`to_dict` 严格往返一致）。

        Args:
            data: :meth:`to_dict` 产出的字典（缺失键取字段默认值）。

        Returns:
            BugTicket 实例。
        """
        data = data or {}
        return cls(
            title=str(data.get("title") or ""),
            severity=str(data.get("severity") or "medium"),
            repro_steps=list(data.get("repro_steps") or []),
            expected=data.get("expected"),
            actual=data.get("actual"),
            root_cause=dict(data.get("root_cause") or {}),
            env=TicketEnv.from_dict(data.get("env")),
            evidence_refs=[
                EvidenceRef.from_dict(e) for e in (data.get("evidence_refs") or [])
            ],
            ticket_id=str(data.get("ticket_id") or ""),
            project=str(data.get("project") or ""),
            case_id=str(data.get("case_id") or ""),
            check_type=str(data.get("check_type") or ""),
            priority=str(data.get("priority") or "P2"),
            impact=str(data.get("impact") or ""),
            minimal_repro=dict(data.get("minimal_repro") or {}),
            triage_category=str(data.get("triage_category") or "UNKNOWN"),
            triage_confidence=float(data.get("triage_confidence") or 0.0),
            auto_fileable=bool(data.get("auto_fileable") or False),
            file_blockers=list(data.get("file_blockers") or []),
            labels=list(data.get("labels") or []),
            created_at=str(data.get("created_at") or ""),
            schema_version=str(data.get("schema_version") or TICKET_SCHEMA_VERSION),
        )

    @classmethod
    def from_json(cls, payload: str) -> "BugTicket":
        """从 JSON 字符串反序列化。"""
        return cls.from_dict(json.loads(payload))

    def to_markdown(self) -> str:
        """渲染为可直接粘贴到 issue 系统的 Markdown 缺陷单。"""
        lines: list[str] = [
            f"# {self.title or '(无标题)'}",
            "",
            f"- **严重级别**：{self.severity}　**初判优先级**：{self.priority}",
            f"- **失败归属**：{self.triage_category}（置信度 {self.triage_confidence:.2f}）",
            f"- **影响面**：{self.impact or 'unknown'}",
            f"- **可自动提单**：{'是' if self.auto_fileable else '否'}",
        ]
        if self.file_blockers:
            lines.append("- **提单阻塞原因**：" + "；".join(self.file_blockers))
        lines.extend(["", "## 环境", "", self.env.to_markdown(), ""])

        # 最小复现路径（D3 收敛结果）优先展示
        mr = self.minimal_repro or {}
        if mr.get("has_repro"):
            lines.append("## 最小复现路径")
            lines.append("")
            preconds = mr.get("preconditions") or []
            if preconds:
                lines.append("**前置条件**：")
                lines.extend(f"- {c}" for c in preconds)
                lines.append("")
            lines.append(
                "**关键路径**（%d 步，收敛率 %.1f%%）："
                % (
                    mr.get("key_step_count", 0),
                    float(mr.get("convergence_ratio") or 0.0) * 100,
                )
            )
            for step in mr.get("key_steps") or []:
                marker = " ← **失败点**" if step.get("is_failure") else ""
                lines.append(f"{step.get('seq')}. {step.get('action_desc')}{marker}")
            lines.append("")

        lines.extend(["## 完整复现步骤", ""])
        if self.repro_steps:
            lines.extend(f"{i}. {s}" for i, s in enumerate(self.repro_steps, 1))
        else:
            lines.append("（无步骤回放来源）")

        lines.extend(
            [
                "",
                "## 预期结果",
                "",
                str(self.expected) if self.expected is not None else "（未记录）",
                "",
                "## 实际结果",
                "",
                str(self.actual) if self.actual is not None else "（未记录）",
                "",
                "## 根因分析",
                "",
            ]
        )
        chain = (self.root_cause or {}).get("root_cause_chain") or []
        if chain:
            for layer in chain:
                lines.append(
                    "%d. **%s**：%s　_(证据: %s)_"
                    % (
                        layer.get("layer", 0),
                        layer.get("label", "-"),
                        layer.get("detail", "-"),
                        layer.get("evidence_ref", "-"),
                    )
                )
        else:
            lines.append("（根因链缺失：证据不足，未硬造根因）")

        lines.extend(["", "## 证据", ""])
        if self.evidence_refs:
            for ev in self.evidence_refs:
                note = f" —— {ev.note}" if ev.note else ""
                lines.append(f"- `{ev.kind}`: {ev.ref}{note}")
        else:
            lines.append("（无证据引用）")

        if self.labels:
            lines.extend(["", "## 标签", "", " ".join(f"`{x}`" for x in self.labels)])
        lines.append("")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# 装配：bug dict → BugTicket
# ═══════════════════════════════════════════════════════════════════════════
def _decide_auto_file(
    triage_category: str,
    triage_confidence: float,
    has_repro: bool,
) -> tuple[bool, list[str]]:
    """判定是否可自动提单，并给出阻塞原因（可解释）。

    规则（CAPABILITY_GAP_PRD D1 + Q7）：
      1. 仅 SUT（被测系统缺陷）可提给开发；ENGINE/ENV/UNKNOWN 不提单。
      2. 分类置信度 ≥ AUTO_FILE_CONFIDENCE_MIN（0.80）。
      3. 必须有可复现路径（否则开发无法接手）。

    Returns:
        (auto_fileable, file_blockers)
    """
    blockers: list[str] = []
    if triage_category not in AUTO_FILE_CATEGORIES:
        blockers.append(
            "失败归属为 %s（非被测系统缺陷），按 D1 不提单给开发，进人工复核队列"
            % triage_category
        )
    if triage_confidence < AUTO_FILE_CONFIDENCE_MIN:
        blockers.append(
            "分类置信度 %.2f < 自动提单下限 %.2f（Q7），进人工复核队列"
            % (triage_confidence, AUTO_FILE_CONFIDENCE_MIN)
        )
    if not has_repro:
        blockers.append("无可复现路径，开发无法接手，需先补齐复现步骤")
    return (not blockers), blockers


def build_ticket_from_bug(
    bug: dict[str, Any],
    env: Optional[TicketEnv] = None,
    max_key_steps: int = DEFAULT_MAX_KEY_STEPS,
) -> BugTicket:
    """把 ``_bug_report.build_bug_report`` 的单条 bug dict 装配为 BugTicket。

    Args:
        bug: build_bug_report()["bugs"] 中的单个元素。
        env: 环境信息；缺省时从 bug 的 project / executed_at 部分回填，其余 unknown。
        max_key_steps: 最小复现关键路径步数上限（默认 3）。

    Returns:
        BugTicket 实例（纯内存对象，不落库）。
    """
    bug = bug or {}
    triage = bug.get("failure_triage") if isinstance(bug.get("failure_triage"), dict) else {}
    severity = str(bug.get("severity") or "medium").lower()
    if severity not in _SEVERITY_TO_PRIORITY:
        severity = "medium"

    # ── 环境：缺省时用 bug 已有字段回填，不臆造 ──
    if env is None:
        env = TicketEnv(
            project=str(bug.get("project") or "unknown"),
            executed_at=str(bug.get("executed_at") or ""),
        )

    repro_steps = [str(s) for s in (bug.get("repro_steps") or [])]

    # ── ★ D3：把回放步骤收敛为 ≤3 步关键路径 + 前置条件 ──
    minimal_repro = converge_repro_strings(
        repro_steps,
        max_key_steps=max_key_steps,
        expected=bug.get("expected"),
        actual=bug.get("actual"),
        error=str(bug.get("failure_reason") or ""),
    )

    # ── 根因：直接复用 triage 的 ≥3 层根因链 ──
    root_cause = {
        "category": triage.get("category", "UNKNOWN"),
        "confidence": float(triage.get("confidence") or 0.0),
        "category_basis": triage.get("category_basis") or [],
        "root_cause_chain": triage.get("root_cause_chain") or [],
        "root_cause_category": bug.get("root_cause_category") or "unclassified",
        "failure_reason": bug.get("failure_reason") or "",
        "triage_version": triage.get("triage_version") or "",
    }

    # ── 证据引用 ──
    evidence: list[EvidenceRef] = []
    if bug.get("execution_id") is not None:
        evidence.append(
            EvidenceRef(
                kind="execution",
                ref="mysql:ai_test_engine.executions.id=%s" % bug.get("execution_id"),
                note="唯一事实源执行记录",
            )
        )
    if bug.get("screenshot"):
        evidence.append(
            EvidenceRef(kind="screenshot", ref=str(bug["screenshot"]), note="失败截图")
        )
    if bug.get("failure_chain"):
        evidence.append(
            EvidenceRef(
                kind="failure_chain",
                ref="executions.failure_chain",
                note=str(bug["failure_chain"])[:200],
            )
        )
    for ref in triage.get("evidence_refs") or []:
        if str(ref) == "no_evidence":
            continue
        evidence.append(
            EvidenceRef(kind="triage_field", ref=str(ref), note="三分类触发证据")
        )

    triage_category = str(triage.get("category") or "UNKNOWN")
    triage_confidence = float(triage.get("confidence") or 0.0)
    auto_fileable, blockers = _decide_auto_file(
        triage_category, triage_confidence, bool(minimal_repro.get("has_repro"))
    )

    case_id = str(bug.get("case_id") or "")
    return BugTicket(
        title=str(bug.get("title") or "[%s] %s" % (severity, case_id or "(未知用例)")),
        severity=severity,
        repro_steps=repro_steps,
        expected=None if bug.get("expected") is None else str(bug.get("expected")),
        actual=None if bug.get("actual") is None else str(bug.get("actual")),
        root_cause=root_cause,
        env=env,
        evidence_refs=evidence,
        ticket_id="BUG-%s" % (bug.get("execution_id") or "UNKNOWN"),
        project=str(bug.get("project") or ""),
        case_id=case_id,
        check_type=str(bug.get("check_type") or ""),
        priority=_SEVERITY_TO_PRIORITY[severity],
        impact=_SEVERITY_TO_IMPACT[severity],
        minimal_repro=minimal_repro,
        triage_category=triage_category,
        triage_confidence=triage_confidence,
        auto_fileable=auto_fileable,
        file_blockers=blockers,
        labels=[
            "severity:%s" % severity,
            "triage:%s" % triage_category.lower(),
            "status:%s" % str(bug.get("status") or "FAIL").lower(),
        ],
        created_at=datetime.now().isoformat(timespec="seconds"),
    )


def build_tickets_from_report(
    report: dict[str, Any],
    env: Optional[TicketEnv] = None,
    max_key_steps: int = DEFAULT_MAX_KEY_STEPS,
) -> dict[str, Any]:
    """把整份 Bug 报告转为可提单的 ticket 集合（含提单闸门统计）。

    Args:
        report: ``_bug_report.build_bug_report`` 的返回值。
        env: 统一环境信息（缺省时逐条从 bug 回填）。
        max_key_steps: 最小复现关键路径步数上限。

    Returns:
        {
          "schema_version": ...,
          "generated_at": ...,
          "ticket_total": int,
          "auto_fileable_total": int,
          "manual_review_total": int,
          "by_priority": {"P1": n, "P2": n, "P3": n},
          "by_triage": {...},
          "tickets": [ticket_dict, ...],
        }
    """
    report = report or {}
    tickets: list[BugTicket] = []
    for bug in report.get("bugs") or []:
        with guard("build_tickets_from_report: tickets.append(build_ticket_from_bug", log=_log, level=logging.WARNING):
            tickets.append(build_ticket_from_bug(bug, env=env, max_key_steps=max_key_steps))

    by_priority: dict[str, int] = {"P1": 0, "P2": 0, "P3": 0}
    by_triage: dict[str, int] = {"ENGINE": 0, "SUT": 0, "ENV": 0, "UNKNOWN": 0}
    auto_total = 0
    for t in tickets:
        by_priority[t.priority] = by_priority.get(t.priority, 0) + 1
        by_triage[t.triage_category] = by_triage.get(t.triage_category, 0) + 1
        if t.auto_fileable:
            auto_total += 1

    return {
        "schema_version": TICKET_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_source": report.get("data_source", "mysql:ai_test_engine.executions"),
        "ticket_total": len(tickets),
        "auto_fileable_total": auto_total,
        "manual_review_total": len(tickets) - auto_total,
        "auto_file_gate": {
            "confidence_min": AUTO_FILE_CONFIDENCE_MIN,
            "allowed_categories": sorted(AUTO_FILE_CATEGORIES),
        },
        "by_priority": by_priority,
        "by_triage": by_triage,
        "tickets": [t.to_dict() for t in tickets],
    }
