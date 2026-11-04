# -*- coding: utf-8 -*-
"""O4 性能相对基线周期校准驱动（定时自动化，联动 target 命令 --perf-recalibrate）。

★ 通用性：本脚本不写死任何靶子清单，动态 import ``target_cfgs.TARGET_CFGS`` 遍历
  全部已注册 target_key（hospital_sut / api_demo / spa_demo / smartshop / 未来新增），
  未来新增靶子注册到 target_cfgs 后无需改本脚本即自动纳入。

★ 每靶子执行：
    python main.py target --target <key> --perf-recalibrate --out logs/reports/calib_<key>.json
  --perf-recalibrate 由 main.py 解析为 cfg_overrides={"perf":{"update_baseline":True}}，
  经 execute(VerificationPlan) 增量合并 → perf_checker 在判定后调 BaselineManager.update()
  回写相对基线（baseline_version +1、原子落盘）。校准不触碰被测系统业务数据；
  服务由 target 命令自动拉起、跑完自动释放。

★ 判定与追溯：
  - 认定校准成功：该靶子 baseline_version +1（持久化生效）且校准 run 无 FAIL
    （summary.failed==0 且无 blocked/error）。若 version 未+1（本轮无有效样本，合法）也
    记为成功但注明。
  - 任一靶子校准 run 引入 FAIL → 判定"存在回归"（真实性能回归或靶子问题），把该靶子
    及原因写入 logs/perf_calib_report.log，并在报告末尾突出提醒，不静默吞掉。
  退出码：0=整体校准 OK；2=存在回归（需人工介入）。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_REPORT_LOG = Path("logs/perf_calib_report.log")
_OUTDIR = Path("logs/reports")
PY = str(Path(os.environ.get("CONDA_PYTHON", "D:/python_tools/anaconda3/python.exe")))

_log = logging.getLogger(__name__)


def _bootstrap() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    for p in (root, root / "src"):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _read_version(target: str) -> int:
    p = Path("src/extensions/perf_baseline/baselines") / f"{target}.json"
    if not p.is_file():
        return 0
    try:
        return int(json.loads(p.read_text(encoding="utf-8")).get("baseline_version", 0))
    except Exception:  # noqa: BLE001
        return -1  # 异常：无法读取，单独记录


def _last_lines(path: Path, n: int = 25) -> list[str]:
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
        return txt.splitlines()[-n:]
    except Exception:  # noqa: BLE001
        return []


def calibrate(target: str) -> dict:
    """跑单个靶子的校准实证，返回结果 dict。"""
    before = _read_version(target)
    out_file = _OUTDIR / f"calib_{target}.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        PY, "main.py", "target", "--target", target,
        "--perf-recalibrate", "--out", str(out_file),
    ]
    tail_before = _last_lines(Path("logs/perf_gate.log")) if Path("logs/perf_gate.log").is_file() else []

    _log.info("[calib] target=%s 运行 %s", target, " ".join(cmd))
    proc = subprocess.run(
        cmd, cwd=".", capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=720,
    )

    after = _read_version(target)
    result: dict = {
        "target": target,
        "before_version": before,
        "after_version": after,
        "exit_code": proc.returncode,
    }

    # 尝试解析机读 JSON
    parsed: dict | None = None
    try:
        if out_file.is_file():
            parsed = json.loads(out_file.read_text(encoding="utf-8"))
    except Exception as _e:  # noqa: BLE001
        result["parse_error"] = str(_e)[:200]

    summary = (parsed or {}).get("summary") or {}
    result["summary"] = summary
    result["cross_cut"] = {k: (parsed or {}).get(k) for k in ("a11y", "visual", "perf", "security")}
    result["all_pass"] = bool(summary.get("all_pass", False))
    result["passed"] = summary.get("passed")
    result["failed"] = summary.get("failed")
    result["blocked"] = summary.get("blocked")
    result["error"] = summary.get("error")

    # 校准成败判定
    version_ok = (after - before) >= 1
    no_fail = result["failed"] in (None, 0) and not result["blocked"] and not result["error"]
    result["version_updated"] = version_ok
    result["regression"] = bool((result["failed"] or 0) > 0 or result["blocked"] or result["error"])
    result["ok"] = not result["regression"] and version_ok

    return result


def main() -> int:
    _bootstrap()
    from src.pipeline.steps.step4.empirical.target_cfgs import TARGET_CFGS

    targets = sorted(TARGET_CFGS.keys())
    if not targets:
        print("未发现任何已注册靶子，中止。", file=sys.stderr)
        return 2

    _OUTDIR.mkdir(parents=True, exist_ok=True)
    _REPORT_LOG.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"\n===== O4 性能基线周期校准 @ {stamp} =====")
    lines.append(f"靶子清单(动态取自 TARGET_CFGS, 共{len(targets)}): {', '.join(targets)}")
    lines.append("-" * 70)

    regressions: list[dict] = []
    okay_targets: list[dict] = []
    for t in targets:
        res = calibrate(t)
        mark = "OK " if res["ok"] else ("!!REGRESSION" if res["regression"] else "!!NO-VERSION")
        lines.append(
            f"[{mark}] {t!r}: baseline_version {res['before_version']} → {res['after_version']} "
            f"(exit={res['exit_code']}, passed={res['passed']}, failed={res['failed']}, "
            f"blocked={res.get('blocked')}, ok={res['all_pass']})"
        )
        if res["regression"]:
            regressions.append(res)
        elif res["version_updated"]:
            okay_targets.append(res)

    if regressions:
        lines.append("\n>>> 存在回归（真实性能回归或靶子问题，需人工介入，不得静默吞掉）：")
        for r in regressions:
            lines.append(
                f"  - {r['target']!r}: exit={r['exit_code']} passed={r['passed']} "
                f"failed={r['failed']} blocked={r['blocked']} error={r['error']}"
            )
            lines.append(
                f"      详情见 logs/reports/calib_{r['target']}.json 与 stdout 末尾："
                + " | ".join(_last_lines(Path("logs/reports") / f"calib_{r['target']}.json", 0))
            )
        verdict = "存在回归（见上方，务必排查）"
        overall_code = 2
    else:
        verdict = "OK（全部靶子校准成功，无新增 FAIL，基线已回写）"
        overall_code = 0

    lines.append("-" * 70)
    lines.append(f"整体校准结果: {verdict}")
    _ver_str = ", ".join(f"{r['target']}=v{r['after_version']}" for r in (regressions or okay_targets)) or "-"
    lines.append(f"各靶子新版本: {_ver_str}")
    for r in regressions + okay_targets:
        lines.append(f"  {r['target']}: baseline v{r['before_version']} → v{r['after_version']}")
    lines.append("=" * 70)

    with open(_REPORT_LOG, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return overall_code


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s: %(message)s")
    sys.exit(main())