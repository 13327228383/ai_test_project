# -*- coding: utf-8 -*-
"""从引擎源码自动提取每个断言/动作工具的参数契约（args.get / check.get 的键）。

用途：驱动器的入参构造以代码事实为准，避免手写参数表造成的「缺少 xxx 参数」假失败。
"""
from __future__ import annotations

import glob
import json
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FILES = sorted(
    glob.glob(
        os.path.join(
            _ROOT,
            "src",
            "pipeline",
            "steps",
            "step4",
            "browser_tools",
            "execution",
            "_single_exec_verify.py",
        )
    )
    + glob.glob(
        os.path.join(
            _ROOT, "src", "pipeline", "steps", "step4", "verification", "_verify_*.py"
        )
    )
)

_Q = r"""["']"""
_KEY_RE = re.compile(r"(?:args|check)\.get\(\s*" + _Q + r"(\w+)" + _Q)
_DEF_RE = re.compile(r"(?:async\s+)?def\s+(_?verify_\w+|api_verify)\s*\(")


def extract() -> dict:
    out: dict[str, dict] = {}
    for f in FILES:
        src = open(f, encoding="utf-8").read()
        matches = list(_DEF_RE.finditer(src))
        for i, m in enumerate(matches):
            name = m.group(1)
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(src)
            body = src[start:end]
            keys = sorted(set(_KEY_RE.findall(body)))
            if keys:
                prev = out.get(name)
                if prev is None or len(keys) > len(prev["keys"]):
                    out[name] = {"keys": keys, "file": os.path.basename(f)}
    return out


if __name__ == "__main__":
    spec = extract()
    for k in sorted(spec):
        print("%-34s %s   [%s]" % (k, ",".join(spec[k]["keys"]), spec[k]["file"]))
    print()
    print("TOTAL", len(spec))
