# -*- coding: utf-8 -*-
"""AutoResearch Ratchet Loop — 自动实验循环脚本。

Karpathy Loop 在测试引擎上的直接落地：
  commit → run engine_eval.py → 对比 baseline → 变好 keep / 变差 revert → repeat

核心职责（三件事）：
  1. 跑评估（调用 engine_eval.py 的 evaluate 函数）
  2. Git 棘轮操作（commit + reset，好的保留、坏的撤销）
  3. 结果记录（results.tsv，每次实验一行）

★ 本脚本**不负责让 AI agent 改代码**。AI agent 的行为由 program.md 驱动，
  ratchet.py 只做"天平 + 棘轮 + 账本"。AI agent 每次改完一行代码后，
  调用 ratchet.py 的 ratchet() 函数即可完成一次实验闭环。

独立运行（手动测试）：
  python autoresearch/ratchet.py init --target smartshop   # 初始化实验分支 + baseline
  python autoresearch/ratchet.py run    # 跑一次评估（不做 git 操作，只看分数）
  python autoresearch/ratchet.py loop   # 进入 ratchet loop（永不停止）
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("autoresearch.ratchet")

# ── 常量 ──────────────────────────────────────────────────────────────────
AUTORESEARCH_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AUTORESEARCH_DIR.parent
RESULTS_TSV = AUTORESEARCH_DIR / "results.tsv"
BRANCH_PREFIX = "autoresearch"


# ══════════════════════════════════════════════════════════════════════════
# ║ 接口兼容层：ratchet.py 旧接口 → engine_eval.py 新 FullEvalResult
# ══════════════════════════════════════════════════════════════════════════


class _EvalAdapter:
    """兼容 ratchet.py 旧接口 (result.score/run_ok/db_ok...)
    到 engine_eval.py 新 FullEvalResult 结构。"""

    def __init__(self, full):
        self._full = full

    def __getattr__(self, name):
        full = self._full
        tr = full.target_result
        mapping = {
            "score": full.overall_score,
            "run_ok": tr.run_ok,
            "db_ok": tr.db_ok,
            "db_score": tr.db_score,
            "pass_rate": tr.pass_rate,
            "false_positive_penalty": tr.false_positive_penalty,
            "avg_confidence": tr.avg_confidence,
            "cross_cut_score": tr.cross_cut_score,
            "overall_score": full.overall_score,
            "target_score": full.target_score,
            "engine_score": full.engine_score,
        }
        if name in mapping:
            return mapping[name]
        return getattr(full, name)


# ══════════════════════════════════════════════════════════════════════════
# ║  Git 操作封装 — 全部走 subprocess，不引入 gitpython 依赖
# ══════════════════════════════════════════════════════════════════════════


def _git(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """执行 git 命令，返回 CompletedProcess。"""
    return subprocess.run(
        ["git"] + args,
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True, check=check,
    )


def git_current_branch() -> str:
    """获取当前分支名。"""
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"], check=False)
    return r.stdout.strip()


def git_is_dirty() -> bool:
    """工作区是否有未提交修改。"""
    r = _git(["status", "--porcelain"], check=False)
    return bool(r.stdout.strip())


def git_create_branch(tag: str) -> str:
    """创建实验分支 autoresearch/<tag> 并切过去。"""
    branch = f"{BRANCH_PREFIX}/{tag}"
    # 先切回 master/main 确保是新分支
    _git(["checkout", "main"], check=False)
    # 如果 main 不存在就用 master
    if _git(["rev-parse", "--verify", "main"], check=False).returncode != 0:
        _git(["checkout", "-b", "main"])
    # 删旧分支（如果存在）
    _git(["branch", "-D", branch], check=False)
    # 创建新分支
    _git(["checkout", "-b", branch])
    _log.info("[ratchet] 创建分支: %s", branch)
    return branch


def git_commit_all(msg: str) -> str:
    """git add -A && git commit，返回短 commit hash。"""
    _git(["add", "-A"], check=False)
    _git(["commit", "-m", msg, "--allow-empty"], check=False)
    r = _git(["rev-parse", "--short", "HEAD"], check=False)
    return r.stdout.strip()


def git_reset_to(commit_hash: str) -> None:
    """git reset --hard 回退到指定 commit。"""
    _git(["reset", "--hard", commit_hash], check=False)
    _log.info("[ratchet] 回退到: %s", commit_hash)


def git_stash() -> None:
    """暂存当前修改（用于评估前清理工作区）。"""
    _git(["stash", "push", "-u", "-m", "ratchet_stash_before_eval"], check=False)


def git_stash_pop() -> None:
    """恢复暂存的修改。"""
    _git(["stash", "pop"], check=False)


# ══════════════════════════════════════════════════════════════════════════
# ║  Ratchet Loop 核心
# ══════════════════════════════════════════════════════════════════════════


def init_experiment(tag: str, target: str = "smartshop") -> dict[str, Any]:
    """初始化实验：建分支 → 跑 baseline → 写 results.tsv header。

    Returns:
        {"branch": str, "baseline_score": float, "baseline_commit": str}
    """
    branch = git_create_branch(tag)

    # 确保 results.tsv 有 header
    if not RESULTS_TSV.exists():
        _write_results_header()

    # 先提交一次初始状态（baseline commit）
    _git(["add", "-A"], check=False)
    _git(["commit", "-m", f"[ratchet] baseline before experiment {tag}", "--allow-empty"], check=False)
    baseline_commit = git_commit_all("[ratchet] baseline snapshot")

    # 跑 baseline 评估
    from autoresearch.engine_eval import evaluate
    _log.info("[ratchet] 跑 baseline 评估...")
    result = _EvalAdapter(evaluate(target))
    baseline_score = result.score

    # 记录 baseline
    _append_result(
        commit=baseline_commit,
        score=baseline_score,
        target=target,
        action="baseline",
        score_delta=0.0,
        description=f"experiment_{tag}_baseline",
    )

    _log.info("[ratchet] ======== BASELINE 建立 ========")
    _log.info("[ratchet]   branch: %s", branch)
    _log.info("[ratchet]   commit: %s", baseline_commit)
    _log.info("[ratchet]   target: %s", target)
    _log.info("[ratchet]   score:  %.2f", baseline_score)
    _log.info("[ratchet] ============================")

    return {
        "branch": branch,
        "baseline_score": baseline_score,
        "baseline_commit": baseline_commit,
        "target": target,
    }


def run_evaluation(target: str = "smartshop") -> dict[str, Any]:
    """跑一次评估，返回 EvaluationResult as dict（不做 git 操作）。

    用于手动测试或在 AI agent 改完代码后调用。
    """
    from autoresearch.engine_eval import evaluate
    result = _EvalAdapter(evaluate(target))
    _log.info("[ratchet] 评估完成: score=%.2f run_ok=%s db_ok=%s",
              result.score, result.run_ok, result.db_ok)
    return {
        "target": target,
        "score": result.score,
        "db_score": result.db_score,
        "pass_rate": result.pass_rate,
        "fp_penalty": result.false_positive_penalty,
        "avg_conf": result.avg_confidence,
        "cross_score": result.cross_cut_score,
        "run_ok": result.run_ok,
        "db_ok": result.db_ok,
    }


def ratchet_step(
    baseline_score: float,
    baseline_commit: str,
    target: str = "smartshop",
    *,
    description: str = "",
) -> dict[str, Any]:
    """执行一次 ratchet step：评估当前代码 → 对比 baseline → keep/revert → 记录。

    这是 AI agent 改完一行代码后应该调用的函数。

    Args:
        baseline_score: 初始化时的 baseline 分数
        baseline_commit: 初始化时的 baseline commit hash（用于回退）
        target: 靶子 key
        description: 本次实验的简短描述

    Returns:
        {
            "kept": bool,       # True=保留了本次修改，False=回退了
            "score": float,     # 本次评估分数
            "delta": float,     # 与 baseline 的差值（可为负）
            "commit": str,      # 本次的 commit hash（如果 kept）
        }
    """
    if not git_is_dirty():
        _log.warning("[ratchet] 工作区干净，没有任何修改 — 跳过 ratchet step")
        return {"kept": False, "score": baseline_score, "delta": 0.0, "commit": ""}

    # 1. 先提交当前修改
    short_before = _git(["rev-parse", "--short", "HEAD"], check=False).stdout.strip()
    experiment_commit = git_commit_all(f"[ratchet] experiment: {description or 'auto_iteration'}")

    # 2. 跑评估
    from autoresearch.engine_eval import evaluate
    result = _EvalAdapter(evaluate(target))
    new_score = result.score
    delta = new_score - baseline_score

    # 3. 决策：keep 还是 revert
    #    判定规则（锁死）：
    #      - new_score > baseline_score + 0.5 → 明显提升，keep
    #      - new_score >= baseline_score - 0.5 → 持平（±0.5 容忍区），keep（不回退）
    #      - new_score < baseline_score - 0.5 → 明显变差，revert
    tolerance = 0.5  # 分数容忍区（避免微小波动导致频繁 revert）

    if delta >= -tolerance:
        # 保持（变好或持平）
        kept = True
        action = "keep"
        _log.info("[ratchet] ✅ KEEP: score %.2f (delta %+.2f) %s",
                  new_score, delta, description)
    else:
        # 回退（明显变差）
        kept = False
        action = "revert"
        _log.info("[ratchet] ❌ REVERT: score %.2f (delta %+.2f → below %.1f tolerance)",
                  new_score, delta, tolerance)
        # 回退到 baseline（不是上一个 commit，因为可能已经连续几次 keep）
        git_reset_to(baseline_commit)

    # 4. 记录结果
    _append_result(
        commit=experiment_commit,
        score=new_score,
        target=target,
        action=action,
        score_delta=delta,
        description=description,
    )

    return {
        "kept": kept,
        "score": new_score,
        "delta": delta,
        "commit": experiment_commit if kept else "",
    }


def ratchet_loop(
    target: str = "smartshop",
    *,
    max_iterations: int = 0,
    tag: str | None = None,
) -> None:
    """进入 ratchet loop（外部驱动循环）。

    这个循环**不自己改代码**——它是"天平 + 棘轮 + 账本"，等待 AI agent
    每改完一次代码后调 ratchet_step()。

    如果你想要"全自动"的体验（AI agent 自己改 + 自己评估），应该把
    program.md 喂给你的 coding agent，让它按 program.md 的指令自动跑。
    ratchet.py 只负责底层机制。

    Args:
        target: 靶子 key
        max_iterations: 最大迭代次数（0 = 永不停止）
        tag: 实验分支 tag（默认用当前时间）
    """
    tag = tag or datetime.now().strftime("%Y%m%d-%H%M")

    # 初始化
    init = init_experiment(tag, target)
    baseline_score = init["baseline_score"]
    baseline_commit = init["baseline_commit"]

    iteration = 0
    _log.info("[ratchet] 进入 ratchet loop — AI agent 每改完一行代码后调 ratchet_step()")
    _log.info("[ratchet] 或手动调用: python autoresearch/ratchet.py step --desc '改了xxx'")

    try:
        while True:
            iteration += 1
            if max_iterations > 0 and iteration > max_iterations:
                _log.info("[ratchet] 达到最大迭代次数 %d，停止", max_iterations)
                break

            # 等待外部信号（AI agent 改代码 → 调用 ratchet_step）
            # 这里提供一个 sleep 等待，实际上应由外部事件触发
            _log.info("[ratchet] --- iteration #%d (waiting for code change) ---", iteration)
            _log.info("[ratchet]   手动触发: python autoresearch/ratchet.py step --desc 'desc'")
            time.sleep(30)  # 每 30 秒检查一次工作区

            if git_is_dirty():
                _log.info("[ratchet] 检测到工作区有修改 → 执行 ratchet step")
                ratchet_step(
                    baseline_score=baseline_score,
                    baseline_commit=baseline_commit,
                    target=target,
                    description=f"auto_iteration_{iteration}",
                )

    except KeyboardInterrupt:
        _log.info("[ratchet] 收到 Ctrl+C，退出 ratchet loop")
    except Exception as e:
        _log.error("[ratchet] loop 异常: %s", e, exc_info=True)

    _log.info("[ratchet] ======== LOOP 结束 ========")
    _log.info("[ratchet] results 已记录到: %s", RESULTS_TSV)


# ══════════════════════════════════════════════════════════════════════════
# ║  结果记录（results.tsv）
# ══════════════════════════════════════════════════════════════════════════


def _write_results_header() -> None:
    """写 results.tsv 的 header 行。"""
    RESULTS_TSV.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "timestamp\tcommit\ttarget\tscore\tscore_delta\taction\tdescription\n"
    )
    RESULTS_TSV.write_text(header, encoding="utf-8")


def _append_result(
    *,
    commit: str,
    score: float,
    target: str,
    action: str,
    score_delta: float,
    description: str,
) -> None:
    """追加一行到 results.tsv。"""
    RESULTS_TSV.parent.mkdir(parents=True, exist_ok=True)
    if not RESULTS_TSV.exists():
        _write_results_header()

    timestamp = datetime.now().isoformat(timespec="seconds")
    row = (
        f"{timestamp}\t{commit[:7]}\t{target}\t{score:.2f}\t"
        f"{score_delta:+.2f}\t{action}\t{description}\n"
    )
    with RESULTS_TSV.open("a", encoding="utf-8") as f:
        f.write(row)


# ══════════════════════════════════════════════════════════════════════════
# ║  CLI 入口 — python autoresearch/ratchet.py <command> [options]
# ══════════════════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ratchet", description="AutoResearch Ratchet Loop")
    p.add_argument("--target", default="smartshop", help="靶子 key")
    p.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    sub = p.add_subparsers(dest="command", required=True)

    # ratchet init
    sp_init = sub.add_parser("init", help="初始化实验分支 + baseline")
    sp_init.add_argument("--tag", default=None, help="分支 tag（默认用时间戳）")

    # ratchet run — 只跑评估（不做 git 操作）
    sub.add_parser("run", help="只跑一次评估（不做 git 操作）")

    # ratchet step — 手动触发一次 ratchet step
    sp_step = sub.add_parser("step", help="手动触发一次 ratchet step")
    sp_step.add_argument("--desc", default="manual_iteration", help="本次实验描述")
    sp_step.add_argument("--baseline-score", type=float, required=True,
                         help="baseline 分数（从 init 输出拿）")
    sp_step.add_argument("--baseline-commit", required=True,
                         help="baseline commit hash（从 init 输出拿）")

    # ratchet loop — 进入自动循环
    sp_loop = sub.add_parser("loop", help="进入 ratchet loop（自动检测工作区）")
    sp_loop.add_argument("--max-iterations", type=int, default=0,
                         help="最大迭代次数（0=永不停止）")
    sp_loop.add_argument("--tag", default=None, help="分支 tag")

    # ratchet status — 查看当前实验状态
    sub.add_parser("status", help="查看当前实验状态 + results.tsv 最后 N 行")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # 确保 autoresearch 和 src 在 sys.path
    sys.path.insert(0, str(AUTORESEARCH_DIR))
    sys.path.insert(0, str(PROJECT_ROOT))

    cmd = args.command

    if cmd == "init":
        tag = args.tag or datetime.now().strftime("%Y%m%d-%H%M")
        info = init_experiment(tag, args.target)
        print("\n✅ 初始化完成")
        print(json.dumps(info, indent=2))

    elif cmd == "run":
        result = run_evaluation(args.target)
        print("\n📊 评估结果")
        print(json.dumps(result, indent=2))

    elif cmd == "step":
        result = ratchet_step(
            baseline_score=args.baseline_score,
            baseline_commit=args.baseline_commit,
            target=args.target,
            description=args.desc,
        )
        print("\n🔄 Ratchet Step 结果")
        print(json.dumps(result, indent=2))

    elif cmd == "loop":
        ratchet_loop(
            target=args.target,
            max_iterations=args.max_iterations,
            tag=args.tag,
        )

    elif cmd == "status":
        print(f"当前分支: {git_current_branch()}")
        print(f"工作区脏: {git_is_dirty()}")
        if RESULTS_TSV.exists():
            lines = RESULTS_TSV.read_text(encoding="utf-8").strip().split("\n")
            print(f"\nresults.tsv ({len(lines)-1} 次实验):")
            print("".join(f"  {l}\n" for l in lines[-11:]))  # 最后 10 次 + header
        else:
            print("\n尚未初始化实验（无 results.tsv）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
