# -*- coding: utf-8 -*-
"""验证充分性核心模块（verification）。

★ 职责：建立「独立真值对照 + 不符即补做」的正式核对台账，对引擎实测的每条用例
  记录「引擎预期 / 独立真值 / 是否一致 / 状态」，并提供受控的补做状态机。

★ 单向依赖：本模块仅依赖标准库 + pydantic，不依赖 pipeline/empirical，
  不被 empirical 反向依赖（避免循环导入）。
"""
from __future__ import annotations

from src.verification.schema import (
    LedgerRecord,
    LedgerStatus,
    TruthSource,
    Consistency,
    Disposition,
)
from src.verification.ledger_store import LedgerStore
from src.verification.remediation import RemediationStateMachine
from src.verification.runner import VerificationRunner
from src.verification.hooks import VerificationHook
from src.verification.aggregate import aggregate_ledger_summary

__all__ = [
    "LedgerRecord",
    "LedgerStatus",
    "TruthSource",
    "Consistency",
    "Disposition",
    "LedgerStore",
    "RemediationStateMachine",
    "VerificationRunner",
    "VerificationHook",
    "aggregate_ledger_summary",
]
