# -*- coding: utf-8 -*-
"""通用根因排查：查找「归一化后比较」与「含大写常量」冲突的同类缺陷。

模式（已在 _verify_storage 中确证为真实 bug）：
    x = (... or "localStorage").strip().lower()     # 归一化成小写
    if x not in ("cookie", "localStorage", ...):    # 与含大写常量比较 → 永假

这类缺陷的特征是「编译无错、运行静默失效」，单靠看代码很难发现，
只有真实执行 + 证据比对才能暴露。本脚本把该模式变成可复用门禁。
"""
from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(_ROOT, "src")

# 捕获：赋值行带 .lower()
_LOWER_ASSIGN = re.compile(r"^\s*(\w+)\s*=\s*.*\.lower\(\)\s*$")
# 捕获：in/not in 一个含大写字面量的元组
_IN_TUPLE = re.compile(r"if\s+.*?\b(\w+)\s+(?:not\s+in|in)\s+\(([^)]*)\)")
_HAS_UPPER = re.compile(r'"[^"]*[A-Z][^"]*"|\'[^\']*[A-Z][^\']*\'')


def scan() -> list[dict]:
    hits: list[dict] = []
    for dirpath, _dn, files in os.walk(SRC):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                lines = open(p, encoding="utf-8").read().splitlines()
            except Exception:
                continue
            lowered: dict[str, int] = {}
            for i, line in enumerate(lines, 1):
                m = _LOWER_ASSIGN.match(line)
                if m:
                    lowered[m.group(1)] = i
                    continue
                m2 = _IN_TUPLE.search(line)
                if m2 and m2.group(1) in lowered and _HAS_UPPER.search(m2.group(2)):
                    hits.append(
                        {
                            "file": os.path.relpath(p, _ROOT),
                            "line": i,
                            "var": m2.group(1),
                            "lowered_at": lowered[m2.group(1)],
                            "code": line.strip()[:110],
                        }
                    )
    return hits


if __name__ == "__main__":
    hits = scan()
    print("命中「lower() 后与含大写常量比较」的可疑点：", len(hits))
    for h in hits:
        print(
            "  %s:%d  var=%s (lower@%d)\n      %s"
            % (h["file"], h["line"], h["var"], h["lowered_at"], h["code"])
        )
    sys.exit(0)
