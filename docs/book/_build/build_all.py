# -*- coding: utf-8 -*-
"""一键重建全部产物。

用法（在仓库根目录执行）：
    python docs/book/_build/build_all.py

依次执行：
  1. gen_file_index.py   AST 全量索引  → _build/file_index.json
  2. gen_appendices.py   机械生成附录 A/B/C
  3. normalize.py        章节标题归一 + 已知勘误
  4. build_md.py         合并单文件 Markdown 全书
  5. build_html.py       生成单文件 HTML 电子书

只改了正文（docs/book/*.md）时，可跳过第 1、2 步：
    python docs/book/_build/build_all.py --skip-scan
"""
from __future__ import annotations

import os
import subprocess
import sys

BUILD = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(script: str) -> None:
    print(f"\n>>> {script}")
    code = subprocess.call([PY, os.path.join(BUILD, script)])
    if code != 0:
        raise SystemExit(f"{script} 退出码 {code}，已中止")


def main() -> None:
    skip = "--skip-scan" in sys.argv
    if not skip:
        run("gen_file_index.py")
        run("gen_appendices.py")
    run("normalize.py")
    run("build_md.py")
    run("build_html.py")
    print("\n完成。")


if __name__ == "__main__":
    main()
