# -*- coding: utf-8 -*-
"""统一配置驱动入口 — 调度各靶子 E2E 流水线。

「易用」目标：
  - 只需一个命令即可跑任意靶子，无需记住 venv python 路径/脚本路径/服务端口。
  - 靶子清单与运行方式由 targets.json 配置驱动；新增靶子只需改配置，不动 run.py。

用法：
  python run.py --list                 # 列出所有靶子与可用性
  python run.py --target spa_demo      # 运行指定靶子（子进程，透传输出与退出码）
  python run.py                        # 顺序运行全部 enabled 靶子，汇总结果
  python run.py --target spa_demo --python <解释器>   # 覆盖靶子 python

退出码：
  0 = 全部成功；1 = 存在失败/目标不可用/参数错误。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGETS_FILE = ROOT / "targets.json"
# 便捷包装统一回落到同一解释器（run.bat 已指向该 venv）；也可由配置/命令行覆盖
DEFAULT_PYTHON = str(ROOT / "target_systems" / "hospital_sut" / ".venv" / "Scripts" / "python.exe")


def _load_targets() -> dict:
    if not TARGETS_FILE.exists():
        print("[run] 未找到配置 %s" % TARGETS_FILE)
        sys.exit(2)
    try:
        return json.loads(TARGETS_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print("[run] 配置解析失败 %s: %s" % (TARGETS_FILE, e))
        sys.exit(2)


def _resolve_python(python_cfg: str, override: str | None) -> str:
    if override:
        return override
    if python_cfg:
        p = python_cfg
        # 相对路径以 Root/… 为准（兼容 wsl 反斜杠/正斜杠）
        return str((ROOT / p) if not Path(p).is_absolute() else Path(p))
    # 配置缺省时：优先全局 venv，其次当前解释器
    if Path(DEFAULT_PYTHON).exists():
        return DEFAULT_PYTHON
    return sys.executable


def _bin_path(exe: str) -> str:
    return str(Path(exe)) if Path(exe).exists() else (shutil.which(exe) or exe)


def list_targets(targets: dict, python_override: str | None) -> int:
    print("== 靶子系统清单（配置驱动入口） ==")
    print("%-12s %-8s %-6s %-6s  %s" % ("target", "kind", "port", "可用", "描述"))
    for name, t in targets.items():
        enabled = bool(t.get("enabled", False))
        avail = "是" if enabled else "否"
        py = _resolve_python(t.get("python", ""), python_override)
        py_ok = "OK" if Path(py).exists() else "缺失(%s)" % py
        print("%-12s %-8s %-6s %-6s  %s (python: %s) [%s]"
              % (name, t.get("kind", "-"), t.get("port", "-"), avail,
                 t.get("description", ""), py_ok, "enabled" if enabled else "disabled"))
    print("\n提示: run.py --target <name>  运行指定靶子；不带参数则顺序运行全部 enabled 靶子。")
    return 0


def run_one(name: str, targets: dict, python_override: str | None) -> int:
    t = targets.get(name)
    if not t:
        print("[run] 未知靶子: %r（可用: %s）" % (name, ", ".join(targets)))
        return 1
    if not t.get("enabled", False):
        reason = t.get("unavailable_reason", "未启用")
        print("[run] 靶子 %r 不可用: %s" % (name, reason))
        return 1
    script = t.get("script", "")
    if not script:
        print("[run] 靶子 %r 未配置 script" % name)
        return 1
    py = _resolve_python(t.get("python", ""), python_override)
    if not Path(py).exists():
        print("[run] 靶子 %r 的解释器不存在: %s" % (name, py))
        return 1
    script_abs = ROOT / script if not Path(script).is_absolute() else Path(script)
    cwd = str(ROOT / (t.get("cwd", ".") or ".")) if t.get("cwd") else str(ROOT)
    cmd = [_bin_path(py), str(script_abs)]
    print("[run] >>> 运行靶子 %r\n      cmd: %s\n      cwd: %s\n" % (name, " ".join(cmd), cwd))
    print("[run] " + ("=" * 78))
    try:
        proc = subprocess.run(cmd, cwd=cwd)
    except FileNotFoundError as e:
        print("[run] 启动失败: %s" % e)
        return 1
    print("[run] " + ("=" * 78))
    print("[run] 靶子 %r 退出码 = %s" % (name, proc.returncode))
    return proc.returncode


def run_all(targets: dict, python_override: str | None) -> int:
    enabled = [(n, t) for n, t in targets.items() if t.get("enabled", False)]
    if not enabled:
        print("[run] 无 enabled 靶子（smartshop 因缺 Django/MySQL 不可用）。")
        return 1
    results = {}
    for name, _t in enabled:
        results[name] = run_one(name, targets, python_override)
    print("\n== 汇总 ==")
    for name, code in results.items():
        print("  %-12s %s" % (name, "OK" if code == 0 else "FAIL(exit=%s)" % code))
    return 0 if all(c == 0 for c in results.values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="run", description="统一配置驱动 E2E 入口")
    parser.add_argument("--target", help="运行指定靶子名")
    parser.add_argument("--python", help="覆盖靶子 python 解释器")
    parser.add_argument("--list", action="store_true", help="列出所有靶子")
    args = parser.parse_args()

    targets = _load_targets()
    if args.list:
        return list_targets(targets, args.python)
    if args.target:
        return run_one(args.target, targets, args.python)
    return run_all(targets, args.python)


if __name__ == "__main__":
    sys.exit(main())