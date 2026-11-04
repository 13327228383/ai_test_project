# -*- coding: utf-8 -*-
"""验证台账命令行入口（verify CLI）。

★ 职责：提供机读（可被 CI 消费）的台账操作：list / add / update-status /
  summary / run。所有输出为 JSON（便于管道与 CI 红灯判定）。

★ 依赖方向：仅依赖标准库 + 本模块（LedgerStore / RemediationStateMachine
  / runner / schema）。

用法示例：
  python -m src.verification.cli list --target edu_sut
  python -m src.verification.cli summary --target edu_sut
  python -m src.verification.cli add --target edu_sut --case P01 \
      --expected '{"status_code":200}'
  python -m src.verification.cli update-status --target edu_sut \
      --case VULN-EDU-01 --to closed --approved-by "总监:Qi"
  python -m src.verification.cli run --target edu_sut
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from src.verification.runner import (
    VerificationRunner,
    parse_spec_truth,
)
from src.verification.ledger_store import LedgerStore
from src.verification.remediation import RemediationStateMachine
from src.verification.schema import LedgerStatus


def _resolve_ledger_dir() -> Path:
    """解析默认台账目录。"""
    cur = Path(__file__).resolve().parent
    return cur / "ledger"


def _emit(obj: Any) -> None:
    """以 JSON 输出到 stdout（机读）。"""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_list(args: argparse.Namespace) -> int:
    """列出某靶子台账（可按 status 筛选）。"""
    store = LedgerStore(_resolve_ledger_dir())
    status = LedgerStatus(args.status) if args.status else None
    recs = store.query(args.target, status=status)
    _emit(
        {
            "target": args.target,
            "count": len(recs),
            "records": [r.to_dict() for r in recs],
        }
    )
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """新增/更新一条 engine_expected 记录。"""
    runner = VerificationRunner(ledger_dir=_resolve_ledger_dir())
    expected = json.loads(args.expected) if args.expected else {}
    rec = runner.write_engine_expected(args.target, args.case, expected)
    _emit({"ok": True, "record": rec.to_dict()})
    return 0


def cmd_update_status(args: argparse.Namespace) -> int:
    """推进某记录的补做状态（guarded 转移需 --approved-by）。"""
    runner = VerificationRunner(ledger_dir=_resolve_ledger_dir())
    try:
        rec = runner.advance(
            args.target,
            args.case,
            to=args.to,
            approved_by=args.approved_by or "",
            owner=args.owner or "",
            disposition=args.disposition or "",
            sla=args.sla or "",
        )
    except ValueError as e:
        _emit({"ok": False, "error": str(e)})
        return 2
    _emit({"ok": True, "record": rec.to_dict()})
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    """导出某靶子准确性汇总报告。"""
    runner = VerificationRunner(ledger_dir=_resolve_ledger_dir())
    report = runner.export_report(args.target)
    _emit(report)
    # CI 红灯：存在未闭环（open/fixing/recheck）即返回非 0。
    if args.ci and (report["open"] + report["fixing"] + report["recheck"]) > 0:
        return 1
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """跑某靶子的验证闭环：写引擎预期（来自 spec 内置映射）→ 比对真值。

    注：真实引擎 empirical 拉起由 VerificationRunner 预留接口；本 CLI 的 run
    直接基于内置真值映射（parse_spec_truth）+ 可选靶子实际探活写入 engine_expected。
    若提供 --live 则会对靶子发起真实 HTTP 请求写入 engine_expected。
    """
    runner = VerificationRunner(ledger_dir=_resolve_ledger_dir())
    target = args.target
    # 真值映射（spec 内置）。
    truths = parse_spec_truth(target)
    # 若 --live，尝试对脆弱点发起真实请求写入 engine_expected。
    if args.live:
        _live_probe(runner, target)
    # 对脆弱点做比对（其余正确端点仅在 live 模式下写入）。
    for case_id, truth in truths.items():
        # 若无 engine_expected，写入一个「探活占位」（仅用于演示闭环）。
        rec = runner.store.get(target, case_id)
        if rec is None:
            runner.write_engine_expected(target, case_id, {"status_code": None})
        try:
            consistent = runner.compare(
                target, case_id,
                truth_value={k: v for k, v in truth.items()
                             if k in ("expected_code", "expect_denied")},
                truth_source="spec",
                truth_detail=truth.get("detail", ""),
            )
        except ValueError as e:
            _emit({"ok": False, "error": str(e)})
            return 2
        _emit({"case_id": case_id, "consistent": consistent.value})
    report = runner.export_report(target)
    _emit({"summary": {k: report[k] for k in (
        "target", "total", "consistent_true", "consistent_false",
        "truth_unavailable", "open", "closed", "known_limitation")}})
    return 0


def _live_probe(runner: VerificationRunner, target: str) -> None:
    """对靶子发起真实 HTTP 请求，写入 engine_expected（live 模式）。"""
    import requests

    from src.pipeline.steps.step4.empirical.target_cfgs import get_target_cfg

    cfg = get_target_cfg(target)
    base = cfg["url"].rstrip("/")
    cases = (cfg.get("verification") or {}).get("cases", [])
    for c in cases:
        try:
            method = c.get("method", "GET").upper()
            path = c.get("path", "")
            body = c.get("body")
            resp = requests.request(method, base + path, json=body, timeout=5)
            runner.write_engine_expected(
                target, c["case_id"],
                {"status_code": resp.status_code, "body": resp.text[:500]},
            )
        except Exception as e:
            runner.write_engine_expected(
                target, c["case_id"], {"status_code": None, "error": str(e)[:200]}
            )


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    p = argparse.ArgumentParser(prog="verify", description="验证台账 CLI")
    sub = p.add_subparsers(dest="command", required=True)

    pl = sub.add_parser("list", help="列出台账记录")
    pl.add_argument("--target", required=True)
    pl.add_argument("--status", default=None, help="按状态筛选(open/fixing/...)")
    pl.set_defaults(func=cmd_list)

    pa = sub.add_parser("add", help="写入 engine_expected")
    pa.add_argument("--target", required=True)
    pa.add_argument("--case", required=True)
    pa.add_argument("--expected", default="{}", help="JSON 字符串")
    pa.set_defaults(func=cmd_add)

    pu = sub.add_parser("update-status", help="推进补做状态")
    pu.add_argument("--target", required=True)
    pu.add_argument("--case", required=True)
    pu.add_argument("--to", required=True, help="目标状态")
    pu.add_argument("--approved-by", default="", help="审批人(guarded 转移必填)")
    pu.add_argument("--owner", default="")
    pu.add_argument("--disposition", default="")
    pu.add_argument("--sla", default="")
    pu.set_defaults(func=cmd_update_status)

    ps = sub.add_parser("summary", help="导出汇总报告")
    ps.add_argument("--target", required=True)
    ps.add_argument("--ci", action="store_true", help="存在未闭环则返回非0")
    ps.set_defaults(func=cmd_summary)

    pr = sub.add_parser("run", help="跑验证闭环(比对脆弱点)")
    pr.add_argument("--target", required=True)
    pr.add_argument("--live", action="store_true", help="对靶子发起真实 HTTP 请求")
    pr.set_defaults(func=cmd_run)

    return p


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
