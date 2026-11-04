# -*- coding: utf-8 -*-
"""智能测试引擎通用命令行入口。

统一两条通用执行路径（主子命令形态）：
  ① 完整 AI 流水线（Step1→Step5，人机协同审核闸门自动放行 green lane）：
       python main.py project --project <engine_project> [--from N]
    说明：engine_project 为引擎 MySQL 项目模型内的项目名（如某靶子的 engine 项目）。
          旧形态 `main.py --project <name>`（无子命令）亦兼容（见 main()）。

  ② 靶子实证（跨系统通用，target 细节由 target_cfgs 驱动，自动拉起被测服务）：
       python main.py target --target <target_key> [--mode browser|api|authz]
                            [--report-dir DIR] [--out FILE.json]
    实现：execute(VerificationPlan(target_key)) —— 引擎实证层统一入口，
          与靶子无关，任何注册到 target_cfgs 的被测系统均可直接跑。

退出码：0=成功；1=参数错误/参数冲突；2=门禁阻断或执行异常；3=存在待人工审核用例。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


def _bootstrap() -> None:
    """把项目根加入 sys.path，保证从任意 cwd 运行均可 import src.*。"""
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _cmd_project(args: argparse.Namespace) -> int:
    """完整 AI 流水线（Step1→5）。"""
    from src.pipeline.full_pipeline_runner import run_full_pipeline

    return run_full_pipeline(args.project, from_step=args.from_step)


def _cmd_target(args: argparse.Namespace) -> int:
    """靶子实证执行（跨系统通用入口）。"""
    from src.pipeline.steps.step4.empirical.plan import VerificationPlan, execute
    from src.pipeline.steps.step4.empirical.target_cfgs import (
        TARGET_CFGS,
        get_target_cfg,
    )

    if args.target not in TARGET_CFGS:
        print(
            "未知 target=%r，可用: %s"
            % (args.target, ", ".join(sorted(TARGET_CFGS.keys()))),
            file=sys.stderr,
        )
        return 1

    plan_kwargs = {"target_key": args.target}
    if args.mode:
        plan_kwargs["mode"] = args.mode
    if args.report_dir:
        out_dir = Path(args.report_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        plan_kwargs["report_path"] = str(out_dir / f"{args.target}_report.json")
    # O4 校准模式：--perf-recalibrate 把 perf.update_baseline=true 注入 cfg_overrides，
    # 使本次 target 实证在判定后回写相对基线（供定时自动化周期性校准长期漂移）。
    if getattr(args, "perf_recalibrate", False):
        plan_kwargs["cfg_overrides"] = {"perf": {"update_baseline": True}}

    try:
        res = execute(VerificationPlan(**plan_kwargs))
    except Exception as e:  # noqa: BLE001 —— 实测路径如实上报失败，不假绿
        print(json.dumps({"ok": False, "target": args.target, "error": str(e)},
                         ensure_ascii=False, indent=2))
        return 2

    summary = (res or {}).get("summary") if isinstance(res, dict) else None
    cross = {k: (res or {}).get(k) for k in ("a11y", "visual", "perf", "security")} \
        if isinstance(res, dict) else None
    payload = {
        "ok": True,
        "target": args.target,
        "mode": (get_target_cfg(args.target) or {}).get("mode"),
        "summary": summary,
        "cross_cut": cross,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    if args.out:
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print("== 结果已写盘: %s" % args.out)
    all_pass = (summary or {}).get("all_pass", False) if isinstance(summary, dict) \
        else False
    return 0 if all_pass else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main",
        description="智能测试引擎通用入口",
    )
    sub = p.add_subparsers(dest="mode", required=True)

    proj = sub.add_parser(
        "project",
        help="完整 AI 流水线（Step1→5，人机协同审核）",
    )
    proj.add_argument("--project", required=True, help="engine 项目名")
    proj.add_argument("--from", dest="from_step", type=int, default=1,
                      help="起始步骤（默认 1）")
    proj.set_defaults(func=_cmd_project)

    tgt = sub.add_parser(
        "target",
        help="靶子实证执行（跨系统通用）",
    )
    tgt.add_argument("--target", required=True, help="target_key（见 target_cfgs 注册表）")
    tgt.add_argument("--mode", choices=["browser", "api", "authz"], default=None)
    tgt.add_argument("--report-dir", default=None, help="报告输出目录")
    tgt.add_argument("--out", default=None, help="机读结果 JSON 落盘路径")
    tgt.add_argument("--perf-recalibrate", action="store_true",
                     help="性能基线校准模式：本次回写相对基线（供定时自动化周期性校准）")
    tgt.set_defaults(func=_cmd_target)

    # 兼容旧文档的 `main.py --project <name>` 形态（直接 --target 顶层参数）。
    p.add_argument("--project", dest="legacy_project", default=None, help=argparse.SUPPRESS)
    p.add_argument("--target", dest="legacy_target", default=None, help=argparse.SUPPRESS)
    return p


def main(argv: list[str] | None = None) -> int:
    _bootstrap()
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "func"):
        return int(args.func(args))
    # 旧形态 `main.py --project <name>` ：映射到 project 子命令。
    if getattr(args, "legacy_project", None):
        from src.pipeline.full_pipeline_runner import run_full_pipeline

        return run_full_pipeline(args.legacy_project, from_step=getattr(args, "from_step", 1) or 1)
    parser.print_help()
    return 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    sys.exit(main())