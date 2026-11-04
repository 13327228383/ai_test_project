# -*- coding: utf-8 -*-
"""把 docs/book/*.md 按顺序合并为单文件 Markdown 全书。

输出：docs/引擎技术设计与实现.md
与 build_html.py 同源：都读 docs/book/ 下的分章源，改章节后两个产物各自重跑即可。
"""
from __future__ import annotations

import datetime
import os
import re

BOOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.path.dirname(BOOK), "引擎技术设计与实现.md")

ORDER = [f"{i:02d}" for i in range(0, 34)] + ["A", "B", "C", "D"]

# 篇次划分（用于目录分组）
PARTS: list[tuple[str, list[str]]] = [
    ("卷首", ["00"]),
    ("第一篇　总览与架构", ["01", "02", "03", "04"]),
    ("第二篇　五步流水线", ["05", "06", "07", "08", "09", "10", "11", "12"]),
    ("第三篇　横切能力", ["13", "14", "15", "16", "17"]),
    ("第四篇　Web 控制台", ["18", "19", "20"]),
    ("第五篇　数据与配置", ["21", "22", "23"]),
    ("第六篇　工程与运维", ["24", "25", "26", "27"]),
    ("第七篇　关键设计与问题解决", ["28", "29", "30", "31", "32", "33"]),
    ("附录", ["A", "B", "C", "D"]),
]


def chapter_files() -> list[tuple[str, str]]:
    files = []
    for fn in os.listdir(BOOK):
        if not fn.endswith(".md"):
            continue
        key = fn.split("-")[0]
        if key in ORDER:
            files.append((key, fn))
    files.sort(key=lambda x: ORDER.index(x[0]))
    return files


def title_of(text: str) -> str:
    first = text.split("\n", 1)[0]
    return first[2:].strip() if first.startswith("# ") else "（无标题）"


def main() -> None:
    files = chapter_files()
    chapters: dict[str, tuple[str, str]] = {}
    for key, fn in files:
        chapters[key] = (fn, open(os.path.join(BOOK, fn), encoding="utf-8").read())

    total_lines = sum(c[1].count("\n") + 1 for c in chapters.values())
    today = datetime.date.today().isoformat()

    out: list[str] = []
    out.append("# E2E 智能测试引擎 · 技术设计与实现")
    out.append("")
    out.append("> 从需求到报告的五个阶段、三层执行通道与三十万行代码背后的设计决策。")
    out.append("")
    out.append("| 项目 | 说明 |")
    out.append("|:---|:---|")
    out.append(f"| 章节 | 34 章正文 + 4 个附录，共 {len(files)} 篇 |")
    out.append("| 覆盖代码 | `src/` 下 1,163 个 Python 文件，约 29.2 万行 |")
    out.append(f"| 正文规模 | {total_lines:,} 行 Markdown 源 |")
    out.append(f"| 成书日期 | {today} |")
    out.append("| 生成方式 | 源码逐模块精读；文件索引与数据表清单由 AST 脚本机械提取 |")
    out.append("")
    out.append("正文中所有机制性结论均标注代码位置（`文件路径:行号`）。"
               "每章末尾固定给出「设计取舍」「本章代码依据」「待核实」三节；"
               "无法确认的内容一律列入「待核实」，不出现在正文结论中。")
    out.append("")

    out.append("---")
    out.append("")
    out.append("## 目录")
    out.append("")
    for part, keys in PARTS:
        keys = [k for k in keys if k in chapters]
        if not keys:
            continue
        out.append(f"**{part}**")
        out.append("")
        for k in keys:
            out.append(f"- [{title_of(chapters[k][1])}](#ch-{k})")
        out.append("")

    for key, _fn in files:
        _name, text = chapters[key]
        out.append("---")
        out.append("")
        out.append(f'<a id="ch-{key}"></a>')
        out.append("")
        out.append(text.rstrip())
        out.append("")

    doc = "\n".join(out)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"输出 {OUT}")
    print(f"合并 {len(files)} 篇，正文源 {total_lines:,} 行，成品 {len(doc):,} 字符 / "
          f"{doc.count(chr(10)) + 1:,} 行")


if __name__ == "__main__":
    main()
