# -*- coding: utf-8 -*-
"""机械核对「书籍正文 vs 代码事实」的覆盖率，输出遗漏清单。

设计要点
--------
- **正文语料 = docs/book/*.md 去掉机械附录 A/B/C**。附录 A（文件索引）、B（数据表）、
  C（接口清单）是全量机械列举，若纳入语料，任何文件/表都会"命中"，从而把
  「正文没讲」伪装成「已覆盖」。D（术语对照）保留，含人工结论。
- 事实源全部来自代码/DDL，不依赖人工印象：
  1. 模块覆盖：file_index.json 的每个路径 → 模块名是否在正文出现。
  2. 目录覆盖：按目录聚合，统计该目录下「正文未提及」的文件数。
  3. 数据表覆盖：schema.sql 的 CREATE TABLE 表名是否在正文出现。
- 只报事实（未提及），不臆断"该不该写"；结论由人按目录重要度判断。

用法：python docs/book/_build/audit_coverage.py [--top N]
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict

BUILD = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.dirname(BUILD)
ROOT = os.path.dirname(os.path.dirname(BOOK))

MECH_APPENDIX = ("A-", "B-", "C-")  # 机械生成，排除出正文语料


def load_corpus() -> str:
    parts: list[str] = []
    for fn in sorted(os.listdir(BOOK)):
        if not fn.endswith(".md"):
            continue
        if fn.startswith(MECH_APPENDIX):
            continue
        with open(os.path.join(BOOK, fn), encoding="utf-8") as fh:
            parts.append(fh.read())
    return "\n".join(parts)


def load_paths() -> list[str]:
    with open(os.path.join(BUILD, "file_index.json"), encoding="utf-8") as fh:
        return list(json.load(fh).keys())


def stem_of(path: str) -> str:
    base = os.path.basename(path)
    return base[:-3] if base.endswith(".py") else base


def package_of(path: str) -> str:
    return os.path.dirname(path)


def _covered(path: str, corpus_low: str, strict: bool) -> bool:
    """一个模块是否「被正文覆盖」。

    - strict=True ：仅看模块名（stem）。噪声大（英文文件名 vs 中文术语对不上）。
    - strict=False（默认）：模块名 OR 所在目录名 OR 路径片段任一命中即算覆盖，
      以削掉「书里写了中文概念、文件名是英文」的假阳性。
    """
    stem = stem_of(path)
    if stem.lower() in corpus_low:
        return True
    if strict:
        return False
    parent = os.path.basename(package_of(path))
    if parent and parent.lower() in corpus_low:
        return True
    frag = path.replace(os.sep, "/").lower()
    if frag in corpus_low:  # 完整相对路径
        return True
    # 路径末两级片段（如 step4/capabilities）出现即算覆盖
    tail = "/".join(frag.split("/")[-2:])
    return tail in corpus_low


def audit_modules(corpus: str, strict: bool = False) -> tuple[list[str], dict[str, list[str]]]:
    paths = [p for p in load_paths() if p.endswith(".py")]
    corpus_low = corpus.lower()
    missing: list[str] = []
    by_dir: dict[str, list[str]] = defaultdict(list)
    for p in paths:
        stem = stem_of(p)
        if stem == "__init__":
            continue
        if not _covered(p, corpus_low, strict):
            missing.append(p)
            by_dir[package_of(p)].append(stem)
    return missing, by_dir


def audit_tables(corpus: str) -> list[str]:
    """从 schema.sql 提取 CREATE TABLE 表名，找正文未提及的。"""
    schema = os.path.join(ROOT, "src", "core", "schema.sql")
    if not os.path.exists(schema):
        return []
    tables: list[str] = []
    with open(schema, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            s = line.strip()
            if s.startswith("--"):  # 注释行不算
                continue
            m = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"']?(\w+)", s, re.I)
            if m:
                tables.append(m.group(1))
    corpus_low = corpus.lower()
    return [t for t in tables if t.lower() not in corpus_low]


def audit_config_keys(corpus: str) -> list[str]:
    """从 pipeline_config.py 提取配置键，找正文未提及的。"""
    cfg = os.path.join(ROOT, "src", "core", "pipeline_config.py")
    if not os.path.exists(cfg):
        return []
    keys: set[str] = set()
    with open(cfg, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            for m in re.finditer(r'["\']([a-z][a-z0-9_]{5,})["\']\s*:', line):
                keys.add(m.group(1))
    corpus_low = corpus.lower()
    return sorted(k for k in keys if k.lower() not in corpus_low)


def main() -> None:
    top = 40
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])

    corpus = load_corpus()
    print(f"正文语料：{len(corpus):,} 字符（已排除机械附录 A/B/C）\n")

    missing, by_dir = audit_modules(corpus, strict=False)
    total_mod = len([p for p in load_paths() if p.endswith(".py") and stem_of(p) != "__init__"])
    print(f"【模块覆盖·宽口径】{total_mod - len(missing)}/{total_mod}（模块名/目录名/路径片段任一命中）；"
          f"未覆盖 {len(missing)} 个\n")
    missing_s, by_dir_s = audit_modules(corpus, strict=True)
    print(f"【模块覆盖·严口径】{total_mod - len(missing_s)}/{total_mod}"
          f"（仅模块名，含英文名/中文术语错配的假阳性）\n")

    # 只列「正文一个模块都没提到」的目录（高信号）；其次列未提及数最多的目录
    silent_dirs: list[str] = []
    heavy_dirs: list[tuple[str, int, int]] = []
    for d, stems in by_dir.items():
        n_files = len([p for p in load_paths()
                       if package_of(p) == d and p.endswith(".py") and stem_of(p) != "__init__"])
        if len(stems) == n_files:
            silent_dirs.append(d)
        heavy_dirs.append((d, len(stems), n_files))
    print(f"═══ 正文『整目录未提及』（该目录下所有模块名正文都没出现）共 {len(silent_dirs)} 个 ═══")
    for d in sorted(silent_dirs):
        print(f"  {d}")
    print()

    heavy_dirs.sort(key=lambda x: -x[1])
    print(f"═══ 未提及模块数最多的目录（Top {top}）═══")
    for d, miss, tot in heavy_dirs[:top]:
        print(f"  {miss:3d}/{tot:3d}  {d}")
    print()

    tabs = audit_tables(corpus)
    print(f"【数据表覆盖】正文未提及的表 {len(tabs)} 个：")
    for t in tabs:
        print(f"  {t}")
    print()

    keys = audit_config_keys(corpus)
    print(f"【配置键覆盖】pipeline_config.py 中正文未提及的键 {len(keys)} 个（前 60）：")
    for k in keys[:60]:
        print(f"  {k}")
    print(f"  … 共 {len(keys)} 个")


if __name__ == "__main__":
    main()
