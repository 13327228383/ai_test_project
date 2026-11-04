# -*- coding: utf-8 -*-
"""台账聚合读函数（aggregate_ledger_summary）—— 流水线强化专项 T1/T5 共用。

★ 职责：读取 JSONL 台账（src/verification/ledger/<target>.jsonl）并聚合，
  供 T1（短路链核对摘要）与 T5（KPI 聚合，唯一事实源）复用。

★ 计数口径：与 VerificationRunner.export_report 完全一致（防口径漂移）——
    - consistent: true / false / truth_unavailable
    - status: open / fixing / recheck / closed / known_limitation
    - closure_rate = closed / (total - truth_unavailable)

★ 依赖方向：仅依赖 verification 层（LedgerStore / schema），不反向依赖 pipeline。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.verification.ledger_store import LedgerStore
from src.verification.schema import Consistency, LedgerStatus

_log = logging.getLogger(__name__)


def _resolve_ledger_dir() -> Path:
    """鲁棒解析默认台账目录（与 VerificationRunner._DEFAULT_LEDGER_DIR 同源）。"""
    cur = Path(__file__).resolve().parent
    # verification/ledger 目录直接存在时优先
    direct = cur / "ledger"
    if direct.is_dir():
        return direct
    # 兜底：项目根 src/verification/ledger
    root = cur.parent.parent.parent
    return root / "src" / "verification" / "ledger"


def _count_record(report: dict[str, Any], rec: Any) -> None:
    """按 export_report 同口径累计单条记录（供 target=None 全量聚合复用）。"""
    if rec.consistent == Consistency.TRUE.value:
        report["consistent_true"] += 1
    elif rec.consistent == Consistency.FALSE.value:
        report["consistent_false"] += 1
    else:
        report["truth_unavailable"] += 1

    if rec.status == LedgerStatus.OPEN.value:
        report["open"] += 1
    elif rec.status == LedgerStatus.FIXING.value:
        report["fixing"] += 1
    elif rec.status == LedgerStatus.RECHECK.value:
        report["recheck"] += 1
    elif rec.status == LedgerStatus.CLOSED.value:
        report["closed"] += 1
    elif rec.status == LedgerStatus.KNOWN_LIMITATION.value:
        report["known_limitation"] += 1


def _finalize(report: dict[str, Any]) -> dict[str, Any]:
    """计算闭环率并按记录数限制 mismatches 明细长度。"""
    decided = report["total"] - report["truth_unavailable"]
    report["closure_rate"] = (
        round(report["closed"] / decided, 4) if decided else 1.0
    )
    # 明细只保留最近 200 条，避免 KPI 响应过大。
    report["mismatches"] = report["mismatches"][:200]
    return report


def aggregate_ledger_summary(
    target: str | None = None, ledger_dir: str | Path | None = None
) -> dict[str, Any]:
    """读取 JSONL 台账并聚合（T1/T5 共用，KPI 台账数据源唯一事实源）。

    Args:
        target: 靶子 key；None 表示聚合全部靶子（遍历 ledger_dir/*.jsonl）。
        ledger_dir: 台账根目录；缺省使用默认目录（src/verification/ledger）。
            仅供测试注入临时目录，不改变对外签名首参。

    Returns:
        {
          "target": str | "*",
          "total": int,                  # 台账总记录数
          "consistent_true": int,        # 一致
          "consistent_false": int,       # 不一致（不符台账数，KPI ledger_mismatch）
          "truth_unavailable": int,      # 真值缺失
          "open": int, "fixing": int, "recheck": int,
          "closed": int, "known_limitation": int,
          "closure_rate": float,         # closed / (total - truth_unavailable)
          "mismatches": [LedgerRecord.to_dict(), ...]  # consistent=="false" 明细
        }
    """
    if ledger_dir is None:
        ledger_dir = _resolve_ledger_dir()
    else:
        ledger_dir = Path(ledger_dir)
    store = LedgerStore(ledger_dir)

    report: dict[str, Any] = {
        "target": target or "*",
        "total": 0,
        "consistent_true": 0,
        "consistent_false": 0,
        "truth_unavailable": 0,
        "open": 0,
        "fixing": 0,
        "recheck": 0,
        "closed": 0,
        "known_limitation": 0,
        "closure_rate": 1.0,
        "mismatches": [],
    }

    if target:
        # 单靶子：直接复用 VerificationRunner.export_report 保证口径一致。
        from src.verification.runner import VerificationRunner

        runner = VerificationRunner(ledger_dir=ledger_dir)
        exp = runner.export_report(target)
        report.update(
            {
                "total": exp["total"],
                "consistent_true": exp["consistent_true"],
                "consistent_false": exp["consistent_false"],
                "truth_unavailable": exp["truth_unavailable"],
                "open": exp["open"],
                "fixing": exp["fixing"],
                "recheck": exp["recheck"],
                "closed": exp["closed"],
                "known_limitation": exp["known_limitation"],
                "closure_rate": exp["closure_rate"],
                "mismatches": [
                    r for r in exp["records"] if r.get("consistent") == Consistency.FALSE.value
                ],
            }
        )
        return _finalize(report)

    # 全量聚合：遍历所有 jsonl（与 LedgerStore.__iter__ 语义一致）
    try:
        jsonl_files = sorted(ledger_dir.glob("*.jsonl"))
    except Exception as e:  # noqa: BLE001
        _log.warning("[aggregate_ledger_summary] 遍历台账目录失败: %s", e)
        jsonl_files = []

    for path in jsonl_files:
        tgt = path.stem
        for rec in store.load(tgt):
            report["total"] += 1
            _count_record(report, rec)
            if rec.consistent == Consistency.FALSE.value:
                report["mismatches"].append(rec.to_dict())

    return _finalize(report)
