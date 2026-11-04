# -*- coding: utf-8 -*-
"""五维实证汇总报告生成器 — 消费 logs/reports/empirical_<key>.json，渲染含
a11y / perf / visual / security / bug 五维证据的统一 HTML 报告。

通用性：不绑定任何靶子，遍历注册的 target_keys，读取各自 empirical JSON 的
横切门禁段，规则化渲染。缺失维度如实显示（如 api 模式无 visual 属诚实）。
落盘 logs/reports/empirical_5dim_YYYYMMDD_HHMMSS.html（旧 4 维文件名保留工具兼容，
新生成统一以 5dim 命名）。

用法：
    python -m scripts.gen_empirical_4dim
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

_log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "logs" / "reports"

TARGET_KEYS = ["hospital_sut", "api_demo", "spa_demo", "smartshop"]
TARGET_LABELS = {
    "hospital_sut": "hospital_sut（患者管理浏览器靶子）",
    "api_demo": "api_demo（REST API 靶子）",
    "spa_demo": "spa_demo（纯前端 SPA 靶子）",
    "smartshop": "smartshop（商品管理浏览器靶子）",
}


def _sev_cn(s: str) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(str(s or "").lower() or "info", str(s or "普通"))


def _load(key: str) -> dict | None:
    p = REPORTS / f"empirical_{key}_report.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        _log.warning("读取 %s 失败: %s", p.name, e)
        return None


def _visual_html(v: dict, mode: str) -> str:
    v = v or {}
    if mode == "api":
        return "<div class='card warn'>API 模式无页面，视觉门禁天然不适用（诚实不入维度）</div>"
    st = v.get("status", "NO_DATA")
    if st == "PASS":
        mark = "<span class='pass'>✅ PASS</span>"
    elif st == "FAIL":
        mark = "<span class='fail'>❌ FAIL</span>"
    else:
        mark = "<span class='warn'>— NO_DATA</span>"
    conf = v.get("confidence", 0.0)
    ev = v.get("evidence") or v.get("reason") or ""
    return (
        f"<div class='card'><b>视觉门禁：</b>{mark} 置信度={conf}"
        f"<br/><div class='small'>{ev}</div></div>"
    )


def _a11y_html(a: dict) -> str:
    a = a or {}
    if not a.get("enabled") or not a.get("findings"):
        return "<div class='card pass'>未发现可访问性缺陷（或未启用）</div>"
    total = a.get("total", len(a.get("findings", [])))
    sev = a.get("by_severity") or {}
    rows = []
    for f in (a.get("findings") or []):
        sev_s = _sev_cn(f.get("severity"))
        cls = "fail" if f.get("severity") == "high" else ("warn" if f.get("severity") == "medium" else "")
        rows.append(
            f"<tr><td>{f.get('rule','')}</td><td class='{cls}'>{sev_s}</td>"
            f"<td><code>{f.get('snippet','')[:60]}</code></td>"
            f"<td>{f.get('description','')}</td></tr>"
        )
    sev_txt = " | ".join(
        f"{_sev_cn(k)}×{v}" for k, v in sev.items()
    )
    return (
        f"<div class='card'><b>可访问性（规则驱动，8 条解释规则）：</b>共 {total} 项"
        f"{'（' + sev_txt + '）' if sev_txt else ''}"
        f"<table><tr><th>规则</th><th>级别</th><th>元素</th><th>说明</th></tr>"
        + "".join(rows) + "</table></div>"
    )


def _perf_html(p: dict) -> str:
    p = p or {}
    s = p.get("summary") or {}
    verdicts = p.get("verdicts") or []
    if not verdicts:
        return "<div class='card warn'>无性能采集样本（检查 perf_collector 插件）</div>"
    rows = []
    for v in verdicts:
        st = v.get("status")
        cls = "pass" if st == "PASS" else "fail"
        m = v.get("metrics") or {}
        rows.append(
            f"<tr><td>{v.get('key','')}</td><td>{v.get('kind','')}</td>"
            f"<td class='{cls} fbold'>{st}</td>"
            f"<td>ttfb={m.get('ttfb_ms')}ms dcl={m.get('dcl_ms')}ms load={m.get('load_ms')}ms clsc={m.get('cls_approx')}</td>"
            f"<td>{'; '.join(v.get('reasons') or [])}</td></tr>"
        )
    return (
        f"<div class='card'><b>性能门禁：</b>通过 {s.get('pass',0)} / 失败 {s.get('fail',0)} / "
        f"总 {s.get('total',0)} | 阻断={bool(s.get('blocking'))}"
        f"<table><tr><th>指标键</th><th>类型</th><th>状态</th><th>指标</th><th>说明</th></tr>"
        + "".join(rows) + "</table></div>"
    )


def _security_html(s: dict) -> str:
    s = s or {}
    if not s.get("enabled") or not s.get("findings"):
        return "<div class='card pass'>未发现安全缺陷（OWASP 规则未命中，或未启用）</div>"
    total = s.get("total", len(s.get("findings", [])))
    sev = s.get("by_severity") or {}
    rows = []
    for f in (s.get("findings") or []):
        sev_s = _sev_cn(f.get("severity"))
        cls = "fail" if f.get("severity") in ("critical", "high") else (
            "warn" if f.get("severity") == "medium" else "")
        rows.append(
            f"<tr><td>{f.get('rule_id','')}</td><td>{f.get('category','')}</td>"
            f"<td class='{cls}'>{sev_s}</td>"
            f"<td>{f.get('evidence','') or f.get('description','')}</td>"
            f"<td>{f.get('description','')}</td></tr>"
        )
    sev_txt = " | ".join(
        f"{_sev_cn(k)}×{v}" for k, v in sev.items()
    )
    return (
        f"<div class='card'><b>安全（OWASP 规则驱动）：</b>共 {total} 项"
        f"{'（' + sev_txt + '）' if sev_txt else ''}"
        f"<table><tr><th>规则</th><th>类别</th><th>级别</th><th>证据</th><th>说明</th></tr>"
        + "".join(rows) + "</table></div>"
    )


def _bug_html(rpt: dict) -> str:
    bugs = []
    for c in (rpt.get("cases") or []):
        if c.get("overall") == "FAIL":
            bugs.append(c)
    if not bugs:
        return "<div class='card pass'>当前批次无 FAIL 用例，Bug 列表为空（诚实）</div>"
    rows = [
        f"<tr><td>{b.get('case','')}</td><td>{b.get('overall')}</td>"
        f"<td>{'; '.join(b.get('trajectory') or [])[:200]}</td></tr>"
        for b in bugs
    ]
    return (
        f"<div class='card fail'><b>Bug / 缺陷：</b>{len(bugs)} 项"
        f"<table><tr><th>用例</th><th>结果</th><th>复现</th></tr>" + "".join(rows) + "</table></div>"
    )


def _gate(r: dict, key: str) -> dict:
    """跨 schema 兼容取横切门禁段。

    新版 execute() 输出把 a11y/visual/perf/security 收敛在 ``cross_cut`` 子键；
    旧版报告（如早期 hospital_sut/api_demo）直接顶层平铺。两版均需兼容，
    否则新产物进入聚合报告会整段读不到而误渲染为"未发现/NO_DATA"。
    """
    cc = r.get("cross_cut")
    if isinstance(cc, dict) and cc.get(key) is not None:
        return cc.get(key) or {}
    v = r.get(key)
    return v if isinstance(v, dict) else {}


def gen() -> dict:
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = REPORTS / f"empirical_5dim_{now}.html"
    sections = []
    grand = {"total": 0, "passed": 0, "failed": 0}
    for key in TARGET_KEYS:
        r = _load(key)
        if not r:
            sections.append(f"<h2>{key}</h2><div class='card warn'>无实证报告（{key} 未运行）</div>")
            continue
        sm = r.get("summary") or {}
        grand["total"] += sm.get("total", 0)
        grand["passed"] += sm.get("passed", 0)
        grand["failed"] += sm.get("failed", 0)
        mode = r.get("mode", "browser")
        rate = (sm.get("passed", 0) / sm.get("total", 1) * 100) if sm.get("total") else 0
        sections.append(
            f"<h2>{TARGET_LABELS.get(key, key)}</h2>"
            f"<div class='card'><b>执行摘要：</b>total={sm.get('total',0)} | "
            f"PASS=<span class='pass'>{sm.get('passed',0)}</span> | "
            f"FAIL=<span class='fail'>{sm.get('failed',0)}</span> | "
            f"通过率 <b>{rate:.1f}%</b> | SUT自启动={bool(r.get('self_started_sut'))} | "
            f"target={r.get('target','')}</div>"
            + _bug_html(r)
            + _visual_html(_gate(r, "visual"), mode)
            + _a11y_html(_gate(r, "a11y"))
            + _security_html(_gate(r, "security"))
            + _perf_html(_gate(r, "perf"))
        )
    g_rate = (grand["passed"] / grand["total"] * 100) if grand["total"] else 0
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"/><title>五维实证汇总报告</title><style>
body{{font-family:'Segoe UI',Microsoft YaHei,sans-serif;margin:32px;background:#f6f8fa;color:#24292f}}
h1{{font-size:24px}}h2{{font-size:18px;margin-top:28px;border-bottom:1px solid #d0d7de;padding-bottom:6px}}
h3{{font-size:15px;margin-top:18px}}
.card{{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:12px 16px;margin-top:10px}}
table{{border-collapse:collapse;width:100%;margin-top:10px;background:#fff}}
th,td{{border:1px solid #d0d7de;padding:6px 10px;font-size:13px;text-align:left;vertical-align:top}}
th{{background:#f0f3f6}}.pass{{color:#1a7f37}}.fail{{color:#cf222e}}.warn{{color:#9a6700}}
.fbold{{font-weight:bold}}.small{{color:#57606a;font-size:12px;margin-top:6px}}
code{{background:#f0f3f6;border-radius:3px;padding:1px 4px}}
</style></head><body>
<h1>多靶子五维实证汇总报告（a11y / perf / visual / security / bug）</h1>
<div class='card'>报告生成: {now} | 引擎: 实证统一入口 execute(VerificationPlan)<br/>
<b>合计：</b>total={grand['total']} | PASS=<span class='pass'>{grand['passed']}</span> |
FAIL=<span class='fail'>{grand['failed']}</span> | 通过率 <b>{g_rate:.1f}%</b><br/>
<span class='small'>证据来源：logs/reports/empirical_*.json（各靶子实证报告，逐 case 结果，可复算）。</span></div>
{''.join(sections)}
</body></html>"""
    out.write_text(html, encoding="utf-8")
    print("REPORT_WRITTEN:", out)
    return {"path": str(out), "summary": grand}


if __name__ == "__main__":
    gen()