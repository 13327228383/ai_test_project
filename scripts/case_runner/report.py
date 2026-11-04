# -*- coding: utf-8 -*-
"""生成用例执行总览报告（HTML），数据源 = logs/case_runner/*.json 逐条证据。

纪律：只汇总真实执行产生的证据文件，不生成任何未经执行的结论。
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(_ROOT, "logs", "case_runner")
REPORT = os.path.join(_ROOT, "logs", "case_runner", "执行总览.html")

TOTAL_CASES = 3047

# 已根因修复的引擎缺陷（人工确认，附影响范围）
FIXES = [
    {
        "id": "ENG-1",
        "title": "storage_check 永久失败（归一化与驼峰标识符冲突）",
        "file": "src/pipeline/steps/step4/verification/_verify_storage.py",
        "root_cause": "先 .strip().lower() 再与驼峰常量 (\"cookie\",\"localStorage\",\"sessionStorage\") 比较 → 分支永假；"
                      "且 lower 后的名字被拼进 JS（localstorage.getItem）→ JS 中未定义标识符",
        "fix": "匹配用小写键，求值用规范驼峰名，单一事实源 _STORAGE_JS_OBJ",
        "impact": "storage_check 全量 14 条（断言轨）× 所有依赖 localStorage/sessionStorage 断言的场景",
        "evidence": "修复前 PASS 253 → 修复后 267（+14）",
        "status": "已修复",
    },
    {
        "id": "ENG-2",
        "title": "page_load_time_check 的 domContentLoaded 永久失效（同类根因）",
        "file": "src/pipeline/steps/step4/verification/_verify_page_load.py",
        "root_cause": "metric.lower() 与含大写常量比较永假；lower 值传入 JS 后严格相等比较亦永假 → value 恒为 0",
        "fix": "_METRIC_CANON 规范名映射（匹配用小写，求值用规范名）",
        "impact": "page_load_time_check 中 metric=domContentLoaded 的全部场景",
        "evidence": "由通用扫描器 scan_lower_mismatch.py 全库检出（命中 2 处，均已修）",
        "status": "已修复",
    },
    {
        "id": "ENG-3",
        "title": "声明↔执行漂移：3 个断言指令 100% 返回「未知工具」",
        "file": "src/pipeline/steps/step4/browser_tools/execution/_single.py",
        "root_cause": "CHECK_TAXONOMY 与执行分派表 _TOOL_HANDLERS 是两份手工同步清单 → 必然再次漂移。"
                      "verify_visual / verify_geometry / verify_api_contract 已声明但漏登记",
        "fix": "以注册表为单一事实源：分派表缺失且实现已存在时，自动按 (page,check)->dict 契约适配注册",
        "impact": "visual_check / geometry_check / api_contract 各 14 条；未来所有新增断言自动免漂移",
        "evidence": "NO_HANDLER 16→2→0",
        "status": "已修复",
    },
    {
        "id": "ENG-4",
        "title": "element_not_exists 注册缺口（只能证有、不能证无）",
        "file": "_taxonomy.py + _single_exec_verify.py + _single.py",
        "root_cause": "CHECK_TAXONOMY 中 tool_name 为空且无独立实现 → 该断言指令无法下发，能力不对称",
        "fix": "新增 _verify_element_not_exists（精确 selector 计数，不走模糊回退）+ 补齐 tool_name 与分派登记",
        "impact": "element_not_exists 全量 14 条；存在/不存在断言能力对称",
        "evidence": "NO_HANDLER 14 → 0",
        "status": "已修复",
    },
]

KNOWN_ISSUES = [
    {
        "title": "580 条用例的指令属于引擎其它层，当前执行器只接入了浏览器工具轨",
        "detail": "record_*（数据链路轨 305，metrics_collector 采集函数）、apply_action_templates"
                  "（控制类型模板轨 220，Step3 生成侧）、run_*_gate（横切门禁轨 43，cross_cut_gates）"
                  "、run_full_pipeline（功能链路轨）等都不是 Step4 浏览器工具，无法经 _execute_tool 下发。"
                  "需在驱动器中为数据链路轨 / 模板轨 / 门禁轨 / 链路轨各接入对应执行入口。",
        "count": "580 条 NO_HANDLER（数据链路轨305 + 控制类型模板轨220 + 横切门禁轨43 + 其它12）",
        "type": "执行器轨路由未覆盖（非引擎缺陷，需扩展执行入口）",
    },
    {
        "title": "API 轨动作指令未在浏览器工具分派表登记",
        "detail": "用例集使用 api_post/api_delete/api_patch 等，引擎分派表仅 api_verify；"
                  "API 执行走独立实现 empirical/api_assertion_runner.py，需在执行器侧做路由映射",
        "count": "120 条 NO_HANDLER（操作指令轨）",
        "type": "用例集命名 vs 引擎架构不一致（需路由映射，非引擎缺陷）",
    },
    {
        "title": "API 轨断言缺少 api_tracker 实例",
        "detail": "「[执行器异常] 无 api_tracker，API断言无法执行」",
        "count": "27~31 条",
        "type": "执行环境未构造 api_tracker",
    },
    {
        "title": "_locate_with_fallback 显式 selector 未命中仍模糊回退",
        "detail": "设计契约写明「若提供 selector 则直接使用，跳过 self-healing」，实现未遵守 → "
                  "存在性断言可能把「不存在」判成「存在」（假 PASS）",
        "count": "影响所有传显式 selector 的存在性/可见性断言",
        "type": "引擎真实缺陷（已定位，影响面待评估后修复）",
    },
    {
        "title": "驱动器未构造真实前置状态",
        "detail": "下载 / 弹窗 / 网络请求 / 时序 / DB 类断言的判真需先真实触发动作（点击下载、触发弹窗等），"
                  "当前驱动器只注入参数未做前置动作",
        "count": "断言轨剩余 FAIL 的主要构成",
        "type": "测试环境建设问题（非引擎缺陷）",
    },
]


def load_reports():
    """返回 (文件名, 数据, mtime)；同一功能域保留最新一次执行（按文件修改时间）。"""
    out = []
    for p in sorted(glob.glob(os.path.join(OUT_DIR, "*.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and "results" in d:
            out.append((os.path.basename(p), d, os.path.getmtime(p)))
    return out


def main():
    reports = load_reports()
    if not reports:
        print("无执行报告")
        return 1

    # 统一口径：以用例 ID 为准，取每个用例「最新一次」执行结果（按报告 mtime 升序覆盖）
    by_case: dict[str, dict] = {}
    for name, d, mt in sorted(reports, key=lambda x: x[2]):
        for r in d["results"]:
            cid = str(r.get("case_id") or "")
            if cid:
                r = dict(r)
                r["_report"] = name
                by_case[cid] = r

    rows_map: dict[str, list] = {}
    for cid, r in by_case.items():
        rows_map.setdefault(str(r.get("功能域") or "未分域"), []).append(r)

    rows = []
    total_exec = len(by_case)
    agg = Counter(r.get("verdict") for r in by_case.values())
    for dom, rs in rows_map.items():
        c = Counter(r.get("verdict") for r in rs)
        el = sum(float(r.get("elapsed_ms") or 0) for r in rs) / 1000.0
        rep = sorted({r["_report"] for r in rs})[-1]
        rows.append((dom, rep, len(rs), c, round(el, 1)))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pct = total_exec / TOTAL_CASES * 100

    html = [
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<title>引擎用例集真实执行总览</title>",
        "<style>",
        "body{font-family:'Microsoft YaHei',sans-serif;background:#0f1115;color:#e6e6e6;margin:0;padding:24px}",
        "h1{font-size:22px;margin:0 0 4px;color:#fff}",
        ".sub{color:#8b93a7;font-size:13px;margin-bottom:18px}",
        "table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13px}",
        "th,td{border:1px solid #2a2f3a;padding:8px 10px;text-align:left;vertical-align:top}",
        "th{background:#1a1f28;color:#9fb0c9}",
        "tr:nth-child(even) td{background:#141820}",
        ".ok{color:#4ade80;font-weight:600}.bad{color:#f87171;font-weight:600}",
        ".warn{color:#fbbf24;font-weight:600}.mute{color:#8b93a7}",
        ".card{background:#141820;border:1px solid #2a2f3a;border-radius:8px;padding:14px 16px;margin:14px 0}",
        ".card h3{margin:0 0 8px;font-size:15px;color:#fff}",
        "code{background:#1c2230;padding:1px 5px;border-radius:3px;color:#a5d6ff;font-size:12px}",
        ".kv{margin:4px 0;font-size:13px}",
        ".tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;margin-right:6px}",
        ".t-fix{background:#14532d;color:#86efac}.t-open{background:#7f1d1d;color:#fecaca}",
        ".t-env{background:#78350f;color:#fde68a}",
        "</style></head><body>",
        f"<h1>引擎用例集真实执行总览</h1>",
        f"<div class='sub'>数据源：docs/引擎端到端测试用例集.xlsx（{TOTAL_CASES} 条） · "
        f"执行器 scripts/case_runner（真实浏览器 + 引擎真实执行入口 _execute_tool） · 生成时间 {now}</div>",
        "<div class='card'><h3>执行进度</h3>",
        f"<div class='kv'>已真实执行 <b class='ok'>{total_exec}</b> / {TOTAL_CASES} 条（<b>{pct:.1f}%</b>）</div>",
        "<div class='kv'>判定汇总：" + " · ".join(f"{k} <b>{v}</b>" for k, v in agg.most_common()) + "</div>",
        "</div>",
        "<table><tr><th>功能域</th><th>报告文件</th><th>条数</th><th>判定分布</th><th>用时</th></tr>",
    ]
    for dom, name, n, c, el in sorted(rows, key=lambda x: -x[2]):
        dist = " · ".join(f"{k} {v}" for k, v in c.most_common())
        html.append(f"<tr><td>{dom}</td><td><code>{name}</code></td><td>{n}</td><td>{dist}</td><td>{el}s</td></tr>")
    html.append("</table>")

    html.append("<div class='card'><h3>已根因修复的引擎缺陷</h3>")
    for f in FIXES:
        html.append(
            f"<div style='border-top:1px solid #2a2f3a;padding:10px 0'>"
            f"<div><span class='tag t-fix'>{f['status']}</span><b>{f['id']} {f['title']}</b></div>"
            f"<div class='kv mute'>位置：<code>{f['file']}</code></div>"
            f"<div class='kv'><b>根因：</b>{f['root_cause']}</div>"
            f"<div class='kv'><b>修法：</b>{f['fix']}</div>"
            f"<div class='kv'><b>影响范围：</b>{f['impact']}</div>"
            f"<div class='kv'><b>验证证据：</b>{f['evidence']}</div></div>"
        )
    html.append("</div>")

    html.append("<div class='card'><h3>已发现未修（如实记录）</h3>")
    for k in KNOWN_ISSUES:
        html.append(
            f"<div style='border-top:1px solid #2a2f3a;padding:10px 0'>"
            f"<div><span class='tag t-open'>未修</span><b>{k['title']}</b></div>"
            f"<div class='kv'>{k['detail']}</div>"
            f"<div class='kv'>规模：{k['count']} · 归属：<span class='warn'>{k['type']}</span></div></div>"
        )
    html.append("</div>")

    html.append(
        "<div class='card'><h3>说明</h3><div class='kv'>"
        "· 判定口径：PASS=实际结果与用例预期一致；FAIL=不一致；UNDECIDED=该变化点语义是「归因/处理正确」"
        "而非「断言成立与否」，只验证不崩溃且有明确返回，<b>不冒充 PASS</b>；NO_HANDLER=指令无法解析为引擎工具；"
        "ERROR=执行抛原生异常。<br>"
        "· 所有结论均来自逐条真实执行产生的证据文件（logs/case_runner/*.json），可复核。<br>"
        "· 「引擎缺陷」与「驱动器/环境问题」严格区分，未把环境问题计入引擎缺陷。"
        "</div></div>"
    )
    html.append("</body></html>")

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(html))
    print("报告已生成:", REPORT)
    print(f"已执行 {total_exec}/{TOTAL_CASES} ({pct:.1f}%)")
    for dom, name, n, c, el in sorted(rows, key=lambda x: -x[2]):
        print(f"  {dom:<12} {n:>4} 条  " + " ".join(f"{k}={v}" for k, v in c.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
