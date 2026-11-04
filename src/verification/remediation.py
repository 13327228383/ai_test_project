# -*- coding: utf-8 -*-
"""补做状态机（RemediationStateMachine）。

★ 职责：以「转移表驱动」实现受控的状态流转，并对进入/离开
  KNOWN_LIMITATION 的转移做 guarded transition 校验（必须 approved_by
  非空，即交付总监审批），杜绝「未审批即标记已知限制」的逃逸。

★ 合法转移（共享知识 §8）：
  OPEN -> FIXING              （指派 owner）
  FIXING -> RECHECK           （重跑）
  RECHECK -> CLOSED           （一致）
  RECHECK -> OPEN             （仍不一致）
  OPEN -> KNOWN_LIMITATION    （guarded: approved_by 非空）
  KNOWN_LIMITATION -> CLOSED  （guarded: approved_by 非空）

★ 关键不变量：任何「进入 KNOWN_LIMITATION」或「从 KNOWN_LIMITATION 出发」
  的转移，approved_by 必须非空，否则拒绝。self-transfer（A->A）一律允许。

★ 依赖方向：仅依赖标准库 + 本模块 schema。
"""
from __future__ import annotations

from typing import Optional

from src.verification.schema import LedgerRecord, LedgerStatus


class RemediationStateMachine:
    """补做状态机（表驱动 + guarded transition）。"""

    # 转移表：(from, to) -> 是否需要 approved_by 审批。
    #   True  = guarded（必须 approved_by 非空）
    #   False = 普通转移（无需审批）
    #   未列入表的转移视为非法。
    _TRANSITIONS: dict[tuple[str, str], bool] = {
        (LedgerStatus.OPEN.value, LedgerStatus.FIXING.value): False,
        (LedgerStatus.FIXING.value, LedgerStatus.RECHECK.value): False,
        (LedgerStatus.RECHECK.value, LedgerStatus.CLOSED.value): False,
        (LedgerStatus.RECHECK.value, LedgerStatus.OPEN.value): False,
        (LedgerStatus.OPEN.value, LedgerStatus.KNOWN_LIMITATION.value): True,
        (
            LedgerStatus.KNOWN_LIMITATION.value,
            LedgerStatus.CLOSED.value,
        ): True,
    }

    # 终态集合（封闭闭环）。CLOSED / KNOWN_LIMITATION 为稳定终态。
    _CLOSED_STATUSES = frozenset(
        {LedgerStatus.CLOSED.value, LedgerStatus.KNOWN_LIMITATION.value}
    )

    def __init__(self) -> None:
        """初始化状态机（转移表在类属性，实例无状态）。"""
        self._transitions = dict(self._TRANSITIONS)

    # ─────────────────────────────────────────────────────────────────
    # 查询
    # ─────────────────────────────────────────────────────────────────
    def can_transition(
        self,
        frm: str | LedgerStatus,
        to: str | LedgerStatus,
        approved_by: str = "",
    ) -> bool:
        """判断 (from -> to) 是否合法（含 guarded 校验）。

        Args:
            frm: 起始状态（枚举或字符串值）。
            to: 目标状态（枚举或字符串值）。
            approved_by: 审批人签名；仅 guarded 转移需要非空。

        Returns:
            True 表示允许转移；False 表示非法或缺少审批。
        """
        from_v = frm.value if isinstance(frm, LedgerStatus) else str(frm)
        to_v = to.value if isinstance(to, LedgerStatus) else str(to)
        # 自转移恒允许。
        if from_v == to_v:
            return True
        # 查找转移表。
        guarded = self._transitions.get((from_v, to_v))
        if guarded is None:
            return False
        # guarded 转移：必须审批人非空。
        if guarded and not approved_by:
            return False
        return True

    def is_closed(self, status: str | LedgerStatus) -> bool:
        """判断状态是否为终态（closed / known_limitation）。"""
        v = status.value if isinstance(status, LedgerStatus) else str(status)
        return v in self._CLOSED_STATUSES

    # ─────────────────────────────────────────────────────────────────
    # 执行转移
    # ─────────────────────────────────────────────────────────────────
    def transition(
        self,
        rec: LedgerRecord,
        to: str | LedgerStatus,
        approved_by: str = "",
        owner: str = "",
        disposition: str = "",
        sla: str = "",
        truth_detail: str = "",
    ) -> LedgerRecord:
        """对记录执行状态转移（就地生成新记录，返回副本）。

        Args:
            rec: 待转移的记录（不被原地修改，返回新 LedgerRecord）。
            to: 目标状态（枚举或字符串值）。
            approved_by: 审批人签名（guarded 转移必填）。
            owner: 可选，转移时指派负责人（写入 owner 字段）。
            disposition: 可选，转移时更新处置口径。
            sla: 可选，转移时更新解决时限。
            truth_detail: 可选，转移时补充真值说明。

        Returns:
            转移后的新 LedgerRecord（status 已更新，updated_at 刷新）。

        Raises:
            ValueError: 转移非法或 guarded 缺少审批。
        """
        from src.verification.schema import _now_iso

        to_v = to.value if isinstance(to, LedgerStatus) else str(to)
        if not self.can_transition(rec.status, to_v, approved_by=approved_by):
            guard_msg = ""
            if self._transitions.get((rec.status, to_v)) is True:
                guard_msg = "（该转移需交付总监审批 approved_by 非空）"
            raise ValueError(
                f"非法状态转移 {rec.status} -> {to_v} "
                f"for case {rec.case_id} {guard_msg}"
            )

        # 构造更新字段（仅覆盖非空项）。
        updated: dict[str, str] = {"status": to_v, "updated_at": _now_iso()}
        if approved_by:
            updated["approved_by"] = approved_by
        if owner:
            updated["owner"] = owner
        if disposition:
            updated["disposition"] = disposition
        if sla:
            updated["sla"] = sla
        if truth_detail:
            updated["truth_detail"] = truth_detail

        payload = rec.to_dict()
        payload.update(updated)
        return LedgerRecord.from_dict(payload)

    def legal_targets(self, frm: str | LedgerStatus) -> list[str]:
        """返回某状态的所有合法目标状态（便于 CLI 提示）。"""
        from_v = frm.value if isinstance(frm, LedgerStatus) else str(frm)
        out: list[str] = []
        for (a, b), guarded in self._transitions.items():
            if a == from_v:
                out.append(b)
        # 自转移
        out.append(from_v)
        return out
