# -*- coding: utf-8 -*-
"""
docs_analytics.fetchers._stats — 统计数据辅助函数

包含：
- _build_module_stats     — 目录名称维度统计
- _build_executor_stats   — 意图建模作者维度统计
- _build_type_stats       — 意图类型维度统计
- _build_risk_level_stats — 风险等级维度统计
- _build_version_stats    — 版本对比维度统计
"""

from datetime import datetime
import logging

_log = logging.getLogger(__name__)


def _build_module_stats(intents):
    lines = [
        "## 📦 目录名称维度",
        "",
        "| 目录名称 | 总数 | 通过 | 失败 | 待验证 | 达成率 |",
        "|------|------|------|------|--------|--------|",
    ]
    mods = {}
    for c in intents:
        m = c.get("catalog_name", "未分类") or "未分类"
        if m not in mods:
            mods[m] = {"total": 0, "pass": 0, "fail": 0, "pending": 0}
        mods[m]["total"] += 1
        st = str(c.get("verification_status", "")).strip().upper()
        if st == "PASS":
            mods[m]["pass"] += 1
        elif st == "FAIL":
            mods[m]["fail"] += 1
        elif not st:
            mods[m]["pending"] += 1
    for m, s in sorted(mods.items()):
        ex = s["total"] - s["pending"]
        pr = ("%.1f%%" % (s["pass"] / ex * 100)) if ex > 0 else "未验证"
        lines.append(
            "| %s | %d | %d | %d | %d | %s |"
            % (m, s["total"], s["pass"], s["fail"], s["pending"], pr)
        )
    return lines + [""]


def _build_executor_stats(intents):
    lines = [
        "## 👤 意图建模作者维度",
        "",
        "| 作者 | 总数 | 通过 | 失败 | 达成率 |",
        "|------|------|------|------|--------|",
    ]
    execs = {}
    for c in intents:
        u = c.get("author", "") or "unknown"
        if u not in execs:
            execs[u] = {"total": 0, "pass": 0, "fail": 0, "pending": 0}
        execs[u]["total"] += 1
        st = str(c.get("verification_status", "")).strip().upper()
        if st == "PASS":
            execs[u]["pass"] += 1
        elif st == "FAIL":
            execs[u]["fail"] += 1
        elif not st:
            execs[u]["pending"] += 1
    for u, s in sorted(execs.items()):
        ex = s["total"] - s["pending"]
        pr = ("%.1f%%" % (s["pass"] / ex * 100)) if ex > 0 else "未验证"
        lines.append(
            "| %s | %d | %d | %d | %s |" % (u, s["total"], s["pass"], s["fail"], pr)
        )
    return lines + [""]


def _build_type_stats(intents):
    lines = [
        "## 🏷️ 意图类型维度",
        "",
        "| 类型 | 总数 | 通过 | 失败 | 待验证 | 达成率 |",
        "|------|------|------|------|--------|--------|",
    ]
    types = {}
    for c in intents:
        t = c.get("case_type", "未分类") or "未分类"
        if t not in types:
            types[t] = {"total": 0, "pass": 0, "fail": 0, "pending": 0}
        types[t]["total"] += 1
        st = str(c.get("verification_status", "")).strip().upper()
        if st == "PASS":
            types[t]["pass"] += 1
        elif st == "FAIL":
            types[t]["fail"] += 1
        elif not st:
            types[t]["pending"] += 1
    for t, s in sorted(types.items()):
        ex = s["total"] - s["pending"]
        pr = ("%.1f%%" % (s["pass"] / ex * 100)) if ex > 0 else "未验证"
        lines.append(
            "| %s | %d | %d | %d | %d | %s |"
            % (t, s["total"], s["pass"], s["fail"], s["pending"], pr)
        )
    return lines + [""]


def _build_risk_level_stats(intents):
    lines = [
        "## 🔴 风险等级维度",
        "",
        "| 等级 | 总数 | 通过 | 失败 | 待验证 | 达成率 |",
        "|------|------|------|------|--------|--------|",
    ]
    pris = {}
    for c in intents:
        p = c.get("risk_level", "P2") or "P2"
        if p not in pris:
            pris[p] = {"total": 0, "pass": 0, "fail": 0, "pending": 0}
        pris[p]["total"] += 1
        st = str(c.get("verification_status", "")).strip().upper()
        if st == "PASS":
            pris[p]["pass"] += 1
        elif st == "FAIL":
            pris[p]["fail"] += 1
        elif not st:
            pris[p]["pending"] += 1
    porder = ["P0", "P1", "P2", "P3", "P4"]
    for p in porder:
        if p in pris:
            s = pris.pop(p)
        else:
            continue
        ex = s["total"] - s["pending"]
        pr = ("%.1f%%" % (s["pass"] / ex * 100)) if ex > 0 else "未验证"
        lines.append(
            "| %s | %d | %d | %d | %d | %s |"
            % (p, s["total"], s["pass"], s["fail"], s["pending"], pr)
        )
    for p, s in sorted(pris.items()):
        ex = s["total"] - s["pending"]
        pr = ("%.1f%%" % (s["pass"] / ex * 100)) if ex > 0 else "未验证"
        lines.append(
            "| %s | %d | %d | %d | %d | %s |"
            % (p, s["total"], s["pass"], s["fail"], s["pending"], pr)
        )
    return lines + [""]


def _build_version_stats():
    lines = ["## 🔄 版本对比维度", ""]
    # ★ 全走 MySQL：测试用例为单表存储，版本对比功能待按 version_id 重设计；
    #   暂返回空版本列表（不再扫磁盘 xlsx），走"仅一个版本"分支
    versions = []
    if len(versions) > 1:
        lines += ["| 版本 | 时间 | 大小 |", "|------|------|------|"]
        for v in versions:
            tstr = datetime.fromtimestamp(v["time"]).strftime("%Y-%m-%d %H:%M")
            kb = v["size"] / 1024
            lines.append("| %s | %s | %.1f KB |" % (v["name"], tstr, kb))
        lines += ["", "> 💡 切换意图资产版本执行后对比各版本达成率变化"]
    else:
        lines += ["仅有一个意图资产版本，无法对比。", ""]
    return lines + [""]
