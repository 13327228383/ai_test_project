# -*- coding: utf-8 -*-
"""对执行报告做根因聚类（区分引擎缺陷 / 驱动器缺陷 / 环境 / 用例语义）。"""
from __future__ import annotations

import collections
import glob
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _norm(s: str) -> str:
    s = str(s or "")
    s = re.sub(r"\d+", "N", s)
    return s[:100]


def classify(row: dict) -> str:
    """按证据文本判定根因归属。"""
    v = row.get("verdict")
    if v == "PASS":
        return "PASS"
    txt = str(row.get("error") or "") + " " + str(row.get("content") or "")
    err = str(row.get("error") or "")

    if v == "NO_HANDLER":
        if "未声明 tool_name" in err:
            return "引擎:注册表缺口(未声明tool_name)"
        return "引擎/用例:指令名无法解析"
    if "未知工具" in txt:
        return "引擎:声明但未接入执行分派表"
    if "Access denied" in txt or "DB轨跳过(环境问题)" in txt:
        return "引擎:DB轨凭据配置错误(fubo残留)"
    if "无 api_tracker" in txt:
        return "引擎:API轨缺api_tracker即失败"
    if re.search(r"缺少\s*\w+\s*参数", txt):
        return "驱动器:入参契约不全"
    if v == "ERROR":
        return "引擎:执行抛原生异常"
    if v == "UNDECIDED":
        return "语义:变化点非判真/判伪(仅验证不崩溃)"
    return "引擎:断言判定不符预期"


def main(pattern: str):
    files = sorted(glob.glob(os.path.join(_ROOT, "logs", "case_runner", pattern)))
    if not files:
        print("no report matched:", pattern)
        return 1
    p = files[-1]
    d = json.load(open(p, encoding="utf-8"))
    rs = d["results"]
    print("报告:", os.path.basename(p), "| 总数", d["total"], "| 用时", d["elapsed_s"], "s")
    print()

    c = collections.Counter(classify(r) for r in rs)
    print("=== 根因归属 ===")
    for k, v in c.most_common():
        print("  %5d  %s" % (v, k))
    print()

    print("=== 非 PASS 明细聚类（Top 20） ===")
    bad = [r for r in rs if r.get("verdict") != "PASS"]
    c2 = collections.Counter(_norm(r.get("error") or r.get("content")) for r in bad)
    for k, v in c2.most_common(20):
        print("  %4d | %s" % (v, k))
    print()

    print("=== 受影响工具（非 PASS） ===")
    c3 = collections.Counter(str(r.get("tool")) for r in bad)
    for k, v in c3.most_common(15):
        print("  %4d | %s" % (v, k))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "*.json"))
