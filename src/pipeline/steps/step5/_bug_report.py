# -*- coding: utf-8 -*-
"""Bug 报告规范 — severity 分级 + 复现步骤 + 根因分类 + 失败三分类/根因下钻（通用规则驱动）。

数据来源：MySQL executions（唯一事实源），status ∈ {FAIL, ERROR} 为 Bug，
SKIP 单独列为跳过项。当前真实库无 FAIL/ERROR 时如实输出空列表，
可用自测样本验证字段结构（禁止假造真实缺陷）。

severity 分级规则（可解释，规则驱动）：
  high   : 数据破坏（写库/insert/update/delete）、安全（注入/越权/权限/sql）、
           核心流程（登录/支付/创建/提交）
  medium : 性能、参数校验、边界值、状态流转
  low    : 文案、样式、拼写、显示
  默认 medium

复现步骤：从 test_cases.steps_json / test_steps / case_actions_json 回放，
并附期望 vs 实际对比（executions.expected/actual）。无法回放时如实注明。

失败三分类 + 根因下钻（P0-6 / P1-5 有界增强，★ [已复核-DeepSeek-V3 2026-08-16] 寇豆码 2026-08-16）：
  每个 Bug 增加 failure_triage 字段（纯增量，不改流水线契约）：
    category        : ENGINE 引擎/断言缺陷 | SUT 被测系统缺陷 | ENV 环境/数据问题 | UNKNOWN(fail-open)
    root_cause_chain: ≥3 层根因链（表层现象 → 中间层（断言/响应差异） → 深层（根因归属）），
                       每层带 evidence_ref 证据引用
  实现位于 src/pipeline/steps/step5/_triage.py（规则驱动、可解释、fail-open 不硬造根因）。

最小复现路径收敛（D3，寇豆码 新增）：
  每个 Bug 增加 minimal_repro 字段（纯增量，不改流水线契约）：
    preconditions / key_steps(≤3) / trigger / assertion / convergence_ratio
  实现位于 src/pipeline/steps/step5/minimal_repro.py:converge_repro_strings。

结构化可提单缺陷单（D4，寇豆码 新增）：
  build_bug_report(..., with_tickets=True) 时额外产出 bug_tickets 字段：
    title / severity / repro_steps / expected / actual / root_cause / env /
    evidence_refs + priority / impact / minimal_repro / auto_fileable
  实现位于 src/pipeline/steps/step5/bug_ticket.py（BugTicket.to_dict/to_json/to_markdown）。

[已复核-DeepSeek-V3 2026-08-16]（severity 规则部分）
★ [已复核-DeepSeek-V3 2026-08-16]（failure_triage 部分；verdict=APPROVED 见 scripts/_tmp/strong_model_review_triage.json）
[未复核-需 GLM/DeepSeek 把关]（minimal_repro / bug_tickets 两处纯增量接入）
"""

import json
from src.core.guarded import guard
import logging
from datetime import datetime
from typing import Any, Optional

_log = logging.getLogger(__name__)

# ★ [已复核-DeepSeek-V3 2026-08-16]：失败三分类 + 根因下钻（P0-6/P1-5，纯增量字段）
from src.pipeline.steps.step5._triage import build_failure_triage, summarize_triage

# ★ D3 最小复现路径收敛（纯增量字段 minimal_repro）[未复核-需 GLM/DeepSeek 把关]
from src.pipeline.steps.step5.minimal_repro import converge_repro_strings

_HIGH_KEYS = (
    "insert", "update", "delete", "写库", "数据破坏", "注入", "越权",
    "权限", "sql", "安全", "登录", "支付", "创建", "提交", "核心流程",
)
_MEDIUM_KEYS = ("性能", "校验", "边界", "状态", "并发", "超时")
_LOW_KEYS = ("文案", "样式", "拼写", "显示", "错别字", "颜色")


def classify_severity(failure_reason: str, failure_category: str, check_type: str, title: str = "") -> str:
    """规则驱动 severity 分级（可解释：命中关键词即可定位到规则）。"""
    hay = " ".join([failure_reason or "", failure_category or "", check_type or "", title or ""]).lower()
    if any(k in hay for k in _HIGH_KEYS):
        return "high"
    if any(k in hay for k in _LOW_KEYS):
        return "low"
    return "medium"


def _step_dict_to_text(it: dict) -> str:
    """把一个步骤 dict 拍平成可读文本，兼容引擎的多种键名约定。

    引擎结构化动作规范（src/架构点.md）采用 ``{type, target_hint, value}``；
    早期 demos 采用 ``{action, target, desc}``。这里做别名兼容，
    避免 bug 报告的复现步骤因键名差异而丢失（通用性修复）。
    """
    verb = it.get("type") or it.get("action") or it.get("操作") or it.get("op") or ""
    target = (
        it.get("target_hint") or it.get("target") or it.get("selector")
        or it.get("url") or it.get("目标") or ""
    )
    value = it.get("value") or it.get("input") or ""
    detail = it.get("description") or it.get("desc") or it.get("描述") or ""

    if not any(str(x).strip() for x in (verb, target, value, detail)):
        return ""
    core = " ".join(p for p in (str(verb), str(target)) if str(p).strip()).strip()
    if str(value).strip():
        core = f"{core} = {value}".strip() if core else str(value).strip()
    if str(detail).strip():
        core = f"{core}（{detail}）".strip() if core else str(detail).strip()
    return core


def _row_to_dict(row, cols: list[str]) -> dict:
    """把一条 cursor 行归一化为 ``{col: value}``。

    ★ 根因修复（2026-09-04）：数据层 ``cursor().fetchall()/fetchone()`` 返回的是
      dict 行（``{'id': ...}``），但本模块此前按元组解包 ``(a, b, ...) = row``
      ——对 dict 迭代得到的是**列名**，导致 case_id/expected/actual 等所有字段
      变成字面列名字符串、severity 退化为 medium、复现步骤失真（假 Bug 报告）。
      这里统一按列名取值，兼容 dict / Row(Bitmap) / tuple 三种形态，跨系统通用。
    """
    if isinstance(row, dict):
        return {c: row.get(c) for c in cols}
    if hasattr(row, "keys"):
        return {c: (row.get(c) if c in row else None) for c in cols}
    # tuple 形态（columns 顺序与 SELECT 一致）
    return {c: (row[i] if i < len(row) else None) for i, c in enumerate(cols)}


def _load_case_steps(conn, case_id: str, project: str) -> list[str]:
    """从 test_cases 回放复现步骤（steps_json / test_steps / case_actions_json）。"""
    steps: list[str] = []
    with guard("_load_case_steps: cur = conn.cursor()", log=_log):
        cur = conn.cursor()
        cur.execute(
            "SELECT steps_json, test_steps, case_actions_json, precondition "
            "FROM test_cases WHERE case_id=%s AND project=%s ORDER BY version_id DESC LIMIT 1",
            (case_id, project),
        )
        row = cur.fetchone()
        if not row:
            return steps
        d = _row_to_dict(
            row, ["steps_json", "test_steps", "case_actions_json", "precondition"]
        )
        steps_json, test_steps, actions_json, precondition = (
            d.get("steps_json"), d.get("test_steps"),
            d.get("case_actions_json"), d.get("precondition"),
        )
        if precondition:
            steps.append(f"前置：{precondition}")
        for blob in (steps_json, test_steps, actions_json):
            if not blob:
                continue
            try:
                parsed = json.loads(blob) if isinstance(blob, (str, bytes)) else blob
            except Exception:
                parsed = None
            items = parsed if isinstance(parsed, list) else ([parsed] if isinstance(parsed, dict) else [])
            before = len(steps)
            for it in items:
                if isinstance(it, dict):
                    txt = _step_dict_to_text(it)
                    if txt:
                        steps.append(txt)
                elif isinstance(it, str) and it.strip():
                    steps.append(it.strip())
            if len(steps) > before:
                break
    return steps


def build_bug_report(
    conn,
    project: Optional[str] = None,
    limit: int = 100,
    with_tickets: bool = False,
) -> dict:
    """从 executions 生成 Bug 报告（FAIL/ERROR 为 Bug，SKIP 单列）。

    Args:
        conn: MySQL 连接（唯一事实源 ai_test_engine）。
        project: 限定项目；None 表示全部项目。
        limit: 最大返回条数。
        with_tickets: ★ D4 —— 为 True 时额外产出 ``bug_tickets``
            （结构化可提单缺陷单集合，见 :mod:`bug_ticket`）。默认 False，
            保持既有调用方返回结构不变（向后兼容）。

    Returns:
        Bug 报告 dict；``with_tickets=True`` 时多一个 ``bug_tickets`` 键。
    """
    cur = conn.cursor()
    where = "status IN ('FAIL','ERROR')"
    params: list = []
    if project:
        where += " AND project=%s"
        params.append(project)
    cur.execute(
        f"SELECT id, project, case_id, check_type, status, expected, actual, details, "
        f"failure_reason, failure_category, failure_chain, screenshot, executed_at "
        f"FROM executions WHERE {where} ORDER BY executed_at DESC LIMIT %s",
        params + [limit],
    )
    bugs: list[dict] = []
    _COLS = ["id", "project", "case_id", "check_type", "status", "expected",
             "actual", "details", "failure_reason", "failure_category",
             "failure_chain", "screenshot", "executed_at"]
    for row in cur.fetchall():
        d = _row_to_dict(row, _COLS)
        eid = d["id"]; proj = d["project"]; case_id = d["case_id"]
        check_type = d["check_type"]; status = d["status"]
        expected = d["expected"]; actual = d["actual"]; details = d["details"]
        failure_reason = d["failure_reason"]; failure_category = d["failure_category"]
        failure_chain = d["failure_chain"]; screenshot = d["screenshot"]
        executed_at = d["executed_at"]
        steps = _load_case_steps(conn, case_id or "", proj or "")
        severity = classify_severity(failure_reason, failure_category, check_type, case_id)
        repro = list(steps)
        if expected is not None or actual is not None:
            repro.append(f"期望：{expected}")
            repro.append(f"实际：{actual}")
        if not repro:
            repro.append("（无步骤回放来源：executions 无步骤、test_cases 无对应记录）")
        # ★ [已复核-DeepSeek-V3 2026-08-16]：失败三分类 + 根因下钻（纯增量字段）
        #   证据全部来自 executions 已落库字段，fail-open（证据不足 → UNKNOWN 不硬造）
        _failure = {
            "status": status,
            "check_type": check_type,
            "expected": expected,
            "actual": actual,
            "details": details,
            "failure_reason": failure_reason,
            "failure_category": failure_category,
            "failure_chain": failure_chain,
            "case_id": case_id,
        }
        _triage = build_failure_triage(_failure)
        # ★ D3：把回放步骤收敛为「≤3 步关键路径 + 前置条件」最小复现清单（纯增量字段）
        #   纯函数、零 DB 写；无可回放步骤时 has_repro=False，不硬造路径
        try:
            _minimal_repro = converge_repro_strings(
                repro,
                expected=expected,
                actual=actual,
                error=failure_reason or "",
            )
        except Exception as _mr_e:  # noqa: BLE001
            _log.debug("最小复现收敛异常(不阻断): %s", str(_mr_e)[:80])
            _minimal_repro = {}
        bugs.append({
            "execution_id": eid,
            "project": proj,
            "case_id": case_id,
            "check_type": check_type,
            "status": status,
            "severity": severity,
            "severity_rule": _severity_rule_note(severity),
            "title": f"[{severity}] {case_id or '(未知用例)'}",
            "repro_steps": repro,
            "expected": expected,
            "actual": actual,
            "root_cause_category": failure_category or "unclassified",
            "failure_reason": failure_reason or "",
            "failure_chain": failure_chain or "",
            "screenshot": screenshot or "",
            "executed_at": str(executed_at),
            "failure_triage": _triage,
            # ★ D3 纯增量字段：最小复现清单（≤3 步关键路径 + 前置条件 + 触发 + 断言）
            "minimal_repro": _minimal_repro,
        })
    # SKIP 单列（非 Bug，但需可见）
    cur.execute(
        f"SELECT case_id, failure_reason, executed_at FROM executions "
        f"WHERE status='SKIP' ORDER BY executed_at DESC LIMIT %s",
        [limit],
    )
    skipped = []
    for _skip_row in cur.fetchall():
        _sd = _row_to_dict(_skip_row, ["case_id", "failure_reason", "executed_at"])
        skipped.append({
            "case_id": _sd.get("case_id"),
            "reason": _sd.get("failure_reason") or "",
            "executed_at": str(_sd.get("executed_at")),
        })
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_source": "mysql:ai_test_engine.executions",
        "scope": "FAIL/ERROR=bug, SKIP=skipped",
        "bug_total": len(bugs),
        "severity_distribution": {
            s: sum(1 for b in bugs if b["severity"] == s) for s in ("high", "medium", "low")
        },
        # ★ [已复核-DeepSeek-V3 2026-08-16]：失败三分类聚合（纯增量字段，0 样本时全 0）
        "triage_distribution": summarize_triage(bugs),
        "bugs": bugs,
        "skipped_total": len(skipped),
        "skipped": skipped,
    }
    # ★ D4：结构化可提单缺陷单（显式开启，避免改变既有调用方返回结构）
    if with_tickets:
        try:
            from src.pipeline.steps.step5.bug_ticket import (
                TicketEnv,
                build_tickets_from_report,
            )

            result["bug_tickets"] = build_tickets_from_report(
                result,
                env=TicketEnv(project=project or "unknown", db="mysql:ai_test_engine"),
            )
        except Exception as _tk_e:  # noqa: BLE001
            _log.warning("[bug_report] 提单装配异常(不阻断): %s", str(_tk_e)[:120])
            result["bug_tickets"] = {"ticket_total": 0, "tickets": [], "error": str(_tk_e)[:200]}
    return result


def _severity_rule_note(severity: str) -> str:
    return {
        "high": "命中数据破坏/安全/核心流程关键词",
        "medium": "性能/校验/边界/状态，或未命中明确关键词的默认级",
        "low": "文案/样式/显示类",
    }[severity]
