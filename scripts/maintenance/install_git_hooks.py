# -*- coding: utf-8 -*-
"""安装/卸载 git pre-commit 钩子：提交涉及 src/frontend/ 的改动时自动跑布局锁校验。

用法:
    python scripts/maintenance/install_git_hooks.py            # 安装
    python scripts/maintenance/install_git_hooks.py --uninstall
    python scripts/maintenance/install_git_hooks.py --status

为什么需要：
    src/frontend/verify_lock.py 的「悬空属性门」检查只有被真正执行才有价值。
    app-design.css 曾长期存在 html:not([data-sb-ready="1"]) 属性门，而全仓库
    没有任何代码设置 data-sb-ready → 该规则恒成立 → 侧栏菜单永久不可点
    （用户表现为「切换功能区点了没反应」），并因为缺乏显式痕迹被反复"修复"仍复发。
    钩子把这条检查前移到提交时刻，避免同类问题再次被合入。

钩子行为：
    - 仅当本次提交涉及 src/frontend/ 下文件时才运行校验（其他改动零开销、零打扰）
    - 校验失败 → 拒绝提交，并打印失败原因
    - 紧急绕过：git commit --no-verify（不推荐）
"""
import os
import stat
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HOOK_PATH = os.path.join(ROOT, ".git", "hooks", "pre-commit")
MARK = "# >>> frontend-lock-guard >>>"

HOOK_BODY = """#!/bin/sh
{mark}
# 由 scripts/maintenance/install_git_hooks.py 安装：前端布局锁 + 悬空属性门校验
# 仅在本次提交涉及 src/frontend/ 时运行；卸载：install_git_hooks.py --uninstall
changed=$(git diff --cached --name-only --diff-filter=ACM -- src/frontend/ 2>/dev/null)
if [ -z "$changed" ]; then
  exit 0
fi

PY=""
# ★ 2026-10-02: 已知可用解释器优先，且必须通过试运行（PATH 上的 python 可能是
#   WindowsApps 残废 stub——存在但退出非 0，导致钩子永远误报校验失败）。
for c in "D:/python_tools/anaconda3/python.exe" \
         "C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe" \
         python python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys" >/dev/null 2>&1; then PY="$c"; break; fi
  if [ -x "$c" ] && "$c" -c "import sys" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "[frontend-lock-guard] 未找到可用的 python 解释器，跳过校验（建议手工执行 src/frontend/verify_lock.py）"
  exit 0
fi

echo "[frontend-lock-guard] 检测到前端改动，运行 src/frontend/verify_lock.py ..."
if ! "$PY" src/frontend/verify_lock.py; then
  echo ""
  echo "[frontend-lock-guard] ❌ 前端布局锁校验未通过，提交已被拒绝。"
  echo "    修复后重试；如确认与前端无关可临时用 git commit --no-verify 绕过（不推荐）。"
  exit 1
fi
echo "[frontend-lock-guard] ✅ 校验通过"
exit 0
# <<< frontend-lock-guard <<<
"""


def install():
    hooks_dir = os.path.dirname(HOOK_PATH)
    if not os.path.isdir(hooks_dir):
        print(f"❌ 找不到 {hooks_dir}（不是 git 仓库？）")
        return 1
    if os.path.exists(HOOK_PATH):
        cur = open(HOOK_PATH, encoding="utf-8", errors="replace").read()
        if MARK not in cur:
            print("❌ 已存在自定义 pre-commit 钩子，为避免覆盖未做修改。")
            print(f"   请手工把 {HOOK_PATH} 里的逻辑合并进现有钩子。")
            return 1
        print("ℹ️  已有旧版本，正在覆盖更新 ...")
    with open(HOOK_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(HOOK_BODY.format(mark=MARK))
    try:
        os.chmod(HOOK_PATH, os.stat(HOOK_PATH).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError as e:
        print(f"⚠️  设置可执行位失败（不影响 Git for Windows）: {e}")
    print(f"✅ 已安装 pre-commit 钩子: {os.path.relpath(HOOK_PATH, ROOT)}")
    return 0


def uninstall():
    if not os.path.exists(HOOK_PATH):
        print("ℹ️  未安装，无需卸载")
        return 0
    cur = open(HOOK_PATH, encoding="utf-8", errors="replace").read()
    if MARK not in cur:
        print("⚠️  现有钩子不是本脚本安装的，未做删除")
        return 1
    os.remove(HOOK_PATH)
    print("✅ 已卸载 pre-commit 钩子")
    return 0


def status():
    if not os.path.exists(HOOK_PATH):
        print("状态: 未安装")
        return 0
    cur = open(HOOK_PATH, encoding="utf-8", errors="replace").read()
    print("状态: {}".format("已安装（本脚本）" if MARK in cur else "存在其他自定义钩子"))
    return 0


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--uninstall":
        return uninstall()
    if arg == "--status":
        return status()
    if arg in ("-h", "--help"):
        print(__doc__)
        return 0
    return install()


if __name__ == "__main__":
    sys.exit(main())
