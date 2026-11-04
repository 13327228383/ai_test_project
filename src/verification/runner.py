# -*- coding: utf-8 -*-
"""验证运行器（VerificationRunner）：引擎接入 + 比对闭环。

★ 职责：把引擎 empirical 的「引擎预期」写入 LedgerStore，读取独立真值
  （来自靶子 spec.md 解析或内置映射），逐用例比对一致性，不一致且非
  truth_unavailable 则经 RemediationStateMachine 置 open 进入补做台账。
  真值缺失时标 truth_unavailable（绝不静默通过）。

★ 流程（对齐 sequenceDiagram）：
  阶段1 写 engine_expected（consistent 初始 truth_unavailable）
  阶段2 读真值 + 比对（一致→closed / 不一致→open+状态机 / 无真值→truth_unavailable）
  阶段3 补做闭环（open→fixing→recheck→closed | known_limitation 关闭 guarded）

★ 依赖方向：仅依赖本模块（LedgerStore / RemediationStateMachine / schema）
  + 标准库 + requests（探活用，可选）+ SutHarness（拉起靶子，可选）。
  不反向依赖 pipeline/empirical。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from src.verification.schema import (
    Consistency,
    Disposition,
    LedgerRecord,
    LedgerStatus,
    TruthSource,
    _now_iso,
)
from src.verification.ledger_store import LedgerStore
from src.verification.remediation import RemediationStateMachine
from src.pipeline.steps.step4.empirical.target_cfgs import get_target_cfg


def _resolve_project_root() -> Path:
    """鲁棒解析项目根：向上找到同时含 src 与 scripts 的祖先。"""
    cur = Path(__file__).resolve().parent
    for _ in range(12):
        if (cur / "src").is_dir() and (cur / "scripts").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    # 兜底：verification 向上 4 级（src/verification -> src -> 根）。
    return Path(__file__).resolve().parent.parent.parent.parent


_ROOT = _resolve_project_root()
_DEFAULT_LEDGER_DIR = _ROOT / "src" / "verification" / "ledger"


class VerificationRunner:
    """验证运行器：引擎预期写入 + 真值比对 + 补做闭环驱动。"""

    def __init__(
        self,
        ledger_dir: str | Path = _DEFAULT_LEDGER_DIR,
        store: Optional[LedgerStore] = None,
    ) -> None:
        """初始化。

        Args:
            ledger_dir: 台账根目录（缺省为 src/verification/ledger）。
            store: 可选外部 LedgerStore（便于测试注入）。
        """
        self.store = store or LedgerStore(ledger_dir)
        self.fsm = RemediationStateMachine()

    # ─────────────────────────────────────────────────────────────────
    # 阶段1：写引擎预期
    # ─────────────────────────────────────────────────────────────────
    def write_engine_expected(
        self,
        target: str,
        case_id: str,
        expected: dict[str, Any],
    ) -> LedgerRecord:
        """将引擎跑完后的预期写入台账。

        若该 (target, case_id) 已存在记录，仅更新 engine_expected 与
        updated_at，并保留原有 truth_value / status（不覆盖历史处置）。
        若不存在则新建一条（consistent 初始为 truth_unavailable，
        status 初始为 open）。

        Args:
            target: 靶子 key。
            case_id: 用例 ID。
            expected: 引擎预期 dict（状态码 / 业务体）。

        Returns:
            写入后的 LedgerRecord。
        """
        existing = self.store.get(target, case_id)
        if existing is not None:
            payload = existing.to_dict()
            payload["engine_expected"] = expected
            payload["updated_at"] = _now_iso()
            rec = LedgerRecord.from_dict(payload)
            self.store.upsert(rec)
            return rec

        rec = LedgerRecord(
            target=target,
            case_id=case_id,
            engine_expected=expected,
            consistent=Consistency.TRUTH_UNAVAILABLE.value,
            status=LedgerStatus.OPEN.value,
        )
        self.store.upsert(rec)
        return rec

    # ─────────────────────────────────────────────────────────────────
    # 阶段2：比对
    # ─────────────────────────────────────────────────────────────────
    def compare(
        self,
        target: str,
        case_id: str,
        truth_value: Optional[dict[str, Any]],
        truth_source: Optional[str] = None,
        truth_detail: str = "",
    ) -> Consistency:
        """比对引擎预期与独立真值，回写台账并驱动补做状态机。

        Args:
            target: 靶子 key。
            case_id: 用例 ID。
            truth_value: 独立真值 dict；None 表示真值缺失。
            truth_source: 真值来源（human/spec/doc）；缺省回退 spec。
            truth_detail: 真值说明/出处。

        Returns:
            Consistency 结论：TRUE / FALSE / TRUTH_UNAVAILABLE。

        Raises:
            ValueError: 该 (target, case_id) 尚无 engine_expected 记录。
        """
        rec = self.store.get(target, case_id)
        if rec is None:
            raise ValueError(
                f"尚未写入引擎预期：target={target} case_id={case_id}"
            )

        # 真值缺失 → 标 truth_unavailable（绝不静默通过）。
        if truth_value is None:
            updated = rec.to_dict()
            updated["consistent"] = Consistency.TRUTH_UNAVAILABLE.value
            updated["truth_value"] = None
            updated["truth_source"] = truth_source
            updated["truth_detail"] = truth_detail
            updated["updated_at"] = _now_iso()
            new_rec = LedgerRecord.from_dict(updated)
            self.store.upsert(new_rec)
            return Consistency.TRUTH_UNAVAILABLE

        consistent = self._is_consistent(rec.engine_expected, truth_value)

        payload = rec.to_dict()
        payload["truth_value"] = truth_value
        payload["truth_source"] = truth_source or TruthSource.SPEC.value
        payload["truth_detail"] = truth_detail
        payload["updated_at"] = _now_iso()

        if consistent:
            payload["consistent"] = Consistency.TRUE.value
            payload["status"] = LedgerStatus.CLOSED.value
            payload["disposition"] = Disposition.NONE.value
        else:
            payload["consistent"] = Consistency.FALSE.value
            payload["status"] = LedgerStatus.OPEN.value
            payload["disposition"] = Disposition.FIX_TARGET.value
            # 经状态机驱动（guarded：OPEN 不需审批，直接通过）。
            new_rec = LedgerRecord.from_dict(payload)
            new_rec = self.fsm.transition(
                new_rec, LedgerStatus.OPEN, disposition=Disposition.FIX_TARGET.value
            )
            self.store.upsert(new_rec)
            return Consistency.FALSE

        new_rec = LedgerRecord.from_dict(payload)
        self.store.upsert(new_rec)
        return Consistency.TRUE

    @staticmethod
    def _is_consistent(engine_expected: dict, truth_value: dict) -> bool:
        """判定引擎预期与真值是否一致。

        比对策略（对齐设计文档待明确事项 2「直接写 dict」）：
          - 若真值含 expected_code，则要求引擎预期含 status_code 且相等。
          - 若真值含 expect_denied=true，则要求引擎预期 status_code 为 4xx/5xx。
          - 其余：逐 key 比对真值中所有字段（引擎预期须包含且相等）。
        """
        # 状态码比对优先。
        if "expected_code" in truth_value:
            eng_code = engine_expected.get("status_code")
            if eng_code is None:
                eng_code = engine_expected.get("code")
            if eng_code != truth_value["expected_code"]:
                return False
        if truth_value.get("expect_denied") is True:
            eng_code = engine_expected.get("status_code")
            if eng_code is None:
                eng_code = engine_expected.get("code")
            if eng_code is None or not (400 <= int(eng_code) < 600):
                return False
        # 逐字段比对真值其余键（status_code / expect_denied 已处理，跳过）。
        # detail/desc 等为可读性元数据，不参与一致性判定（避免污染 true/false）。
        _META_KEYS = ("detail", "desc", "comment", "reason")
        for k, v in truth_value.items():
            if k in ("expected_code", "expect_denied") or k in _META_KEYS:
                continue
            if k not in engine_expected or engine_expected[k] != v:
                return False
        return True

    # ─────────────────────────────────────────────────────────────────
    # 阶段3：补做闭环（驱动状态机）
    # ─────────────────────────────────────────────────────────────────
    def advance(
        self,
        target: str,
        case_id: str,
        to: str | LedgerStatus,
        approved_by: str = "",
        owner: str = "",
        disposition: str = "",
        sla: str = "",
    ) -> LedgerRecord:
        """推进某记录的补做状态（open→fixing→recheck→closed 等）。

        Args:
            target: 靶子 key。
            case_id: 用例 ID。
            to: 目标状态。
            approved_by: 审批人（known_limitation 关闭必填）。
            owner/sla/disposition: 可选补充字段。

        Returns:
            转移后的新 LedgerRecord。
        """
        rec = self.store.get(target, case_id)
        if rec is None:
            raise ValueError(
                f"记录不存在：target={target} case_id={case_id}"
            )
        new_rec = self.fsm.transition(
            rec, to, approved_by=approved_by, owner=owner,
            disposition=disposition, sla=sla,
        )
        self.store.upsert(new_rec)
        return new_rec

    # ─────────────────────────────────────────────────────────────────
    # 批量跑账（含可选引擎拉起）
    # ─────────────────────────────────────────────────────────────────
    def run_ledger(self, target: str) -> list[LedgerRecord]:
        """加载并返回某靶子全部台账记录（按落盘顺序）。"""
        return self.store.load(target)

    def export_report(self, target: str) -> dict[str, Any]:
        """导出某靶子的准确性汇总报告（供交付总监审阅 / CI 消费）。

        Returns:
            报告 dict：{target, total, consistent_true, consistent_false,
                       truth_unavailable, open, fixing, recheck, closed,
                       known_limitation, records:[...]}。
        """
        recs = self.store.load(target)
        report: dict[str, Any] = {
            "target": target,
            "total": len(recs),
            "consistent_true": 0,
            "consistent_false": 0,
            "truth_unavailable": 0,
            "open": 0,
            "fixing": 0,
            "recheck": 0,
            "closed": 0,
            "known_limitation": 0,
            "records": [r.to_dict() for r in recs],
        }
        for r in recs:
            if r.consistent == Consistency.TRUE.value:
                report["consistent_true"] += 1
            elif r.consistent == Consistency.FALSE.value:
                report["consistent_false"] += 1
            else:
                report["truth_unavailable"] += 1
            if r.status == LedgerStatus.OPEN.value:
                report["open"] += 1
            elif r.status == LedgerStatus.FIXING.value:
                report["fixing"] += 1
            elif r.status == LedgerStatus.RECHECK.value:
                report["recheck"] += 1
            elif r.status == LedgerStatus.CLOSED.value:
                report["closed"] += 1
            elif r.status == LedgerStatus.KNOWN_LIMITATION.value:
                report["known_limitation"] += 1
        # 闭环率：closed / (total - truth_unavailable)
        decided = report["total"] - report["truth_unavailable"]
        report["closure_rate"] = (
            round(report["closed"] / decided, 4) if decided else 1.0
        )
        return report


# ─────────────────────────────────────────────────────────────────────
# spec.md 真值解析（从靶子 spec.md 提取脆弱点真值，作为 truth_source=spec）
# ─────────────────────────────────────────────────────────────────────
def parse_spec_truth(target: str) -> dict[str, dict[str, Any]]:
    """从 target_cfg["verification"]["cases"] 聚合脆弱点真值映射（单一真值源）。

    去靶子化：不再按 target 名 if/elif 内置映射，而是读取各靶子在 target_cfgs
    声明的 verification.cases[*].truth_value。键为 case_id，值为 truth_value dict
    （含 expected_code / expect_denied / detail）。

    Args:
        target: 靶子 key（edu_sut / fin_sut 等）。

    Returns:
        {case_id: truth_value_dict} 映射。靶子无 verification 配置或无脆弱点时返回空 dict。
    """
    try:
        cfg = get_target_cfg(target)
    except Exception:
        return {}
    cases = (cfg.get("verification") or {}).get("cases") or []
    truth_map: dict[str, dict[str, Any]] = {}
    for c in cases:
        truth = c.get("truth_value")
        if truth:
            truth_map[c["case_id"]] = dict(truth)
    return truth_map
