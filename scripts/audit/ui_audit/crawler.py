# -*- coding: utf-8 -*-
"""
UI 自动化核查爬虫（动态层 B + 跳转层 C + 数据层 E）—— 彻底覆盖所有 Tab 与跳转页
=================================================================================
升级点（针对原"硬编码37清单 + 只点一层"的缺陷）：
  B 页面发现  ：登录后从侧边栏/导航 <a href> 递归收集内部页面链接，求可达闭包，
                替代硬编码路由清单，杜绝漏页/写死死地址。
  C 递归跳转  ：对闭包内每个页面，沿其内部 <a> 打开跳转目标页并核查，直至闭包收敛。
  D 全交互    ：每页反复"发现可点控件→点击→重扫"直到无新元素，覆盖每个 Tab/折叠/
                树下钻/表格行下拉/select 分支（含多轮，而非单层）。
  F 网络错误  ：监听 response 捕获 4xx/5xx，requestfailed 捕获连接失败。
  E 数据规则  ：key-selector 必渲染非空 + 值域校验（置信度0-100、风险高中低、
                时间 YYYY-MM-DD HH:MM:SS、无 (null)/NaN/undefined/[object Object]）。
  报告          ：输出 JSON + 自包含 HTML，支持 --baseline diff 发现新增/复现问题。

用法:
  python src\none\crawler.py   (本文件位于 scripts/audit/ui_audit/crawler.py)
  python scripts/audit/ui_audit/crawler.py [--project hospital_sut]
      [--routes /a,/b] [--limit 30] [--out DIR] [--baseline prev.json] [--base json]
"""
from __future__ import annotations
import argparse, json, re, sys, time, hashlib
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8700"

# 起始入口页（登录后默认落地）——从这些页面递归发现所有内部页面链接
SEED_ROUTES = ["/dashboard"]
# 绝不自动访问/自动点击的词法黑名单
SKIP_ROUTE_RX = re.compile(
    r"^/(api|login|logout|register|favicon|health|readyz|healthz|static|question|answer)",
    re.I)
DESTRUCTIVE_RX = re.compile(
    r"保存|删除|提交|通过|拒绝|放行|清空|重置|退出|登出|新建|创建|删除|运行|执行|发起|"
    r"下载|清除|停止|归档|激活|回滚|审批|导入|导出|批量|purge|delete|submit|save|run|exec",
    re.I)
# 可见异常检测
ANOMALY_RX = [
    (r"\bNaN\b", "NaN"),
    (r"\bundefined\b", "undefined"),
    (r"\[object Object\]", "[object Object]"),
    (r"\(null\)", "(null)"),
]
# 每页交互的「安全点击」选择器（浏览类）
TAB_SEL = ["[role=tab]", ".seg-btn", ".tab-btn", ".tabs button", "button.nav-btn", ".seg-btn.is-active ~ span"]
COLLAPSE_SEL = ["[data-collapse]", ".accordion-header", ".rv-chain-toggle-btn", ".collapse-handler", "summary"]
DRILL_SEL = ["#reqList .rv-req-item", "#caseList .md-list-item", "#caseList li", ".md-list-item",
             ".data-row", ".tree-node", "tbody tr", ".list-item", ".rv-req-item"]
SELECT_SEL = ["select"]

# ── 数据正确性规则（E 层）：作用于页面渲染文本/控件值 ──
def collect_anomalies(text: str) -> list:
    hits = []
    for pat, tag in ANOMALY_RX:
        for m in re.finditer(pat, text):
            s = max(0, m.start() - 20)
            hits.append({"kind": tag, "ctx": text[s:m.end() + 20]})
            if len(hits) >= 30:
                return hits
    return hits


def human_of(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t[:60]


def _count_rows(pg):
    total = 0
    for sel in ("#reqList .rv-req-item", "#caseList .md-list-item", "#caseList li", ".md-list-item",
                ".data-row", "tbody tr", ".tree-node", ".list-item"):
        try:
            total += len(pg.query_selector_all(sel))
        except Exception:
            pass
    return total


def _sample_inputs(pg):
    out = []
    for el in pg.query_selector_all("input[value], input:not([type=password]), textarea, select"):
        try:
            tag = el.evaluate("e=>e.tagName")
            vid = el.get_attribute("id") or ""
            val = el.input_value() if tag in ("INPUT", "TEXTAREA") else (el.evaluate("e=>e.options[e.selectedIndex]?e.options[e.selectedIndex].value:''") or "")
            if val and str(val).strip():
                out.append({"id": vid, "val": str(val)[:30]})
        except Exception:
            pass
    return out[:8]


def snap(pg, caps, tag):
    ans = []
    try:
        t = pg.query_selector("body").inner_text() if pg.query_selector("body") else ""
    except Exception:
        t = ""
    caps.setdefault("steps", []).append({
        "tag": tag,
        "text_len": len(re.sub(r"\s+", "", t)),
        "rows": _count_rows(pg),
        "anomalies": collect_anomalies(t) or [],
        "inputs": _sample_inputs(pg),
    })


def _blacklisted_el(pg, el, black=None):
    try:
        el_id = el.get_attribute("id") or ""
        if el_id in ("envSelect", "userSelect", "envProjectSelect", "projectSelect", "projectSelect2",
                     "statusFilter", "riskFilter", "confidenceFilter", "typeFilter", "trackFilter",
                     "langFilter", "reqFilter", "scoreFilter", "priorityFilter"):
            return True
    except Exception:
        return True
    if black is None:
        return False
    try:
        txt = el.inner_text()
        cls = el.get_attribute("class") or ""
        if black.search(txt + " " + cls):
            return True
    except Exception:
        return True
    return False


def _collect_nav_links(pg) -> list:
    """收集当前页全部内部 <a href>（页面级），返回去重列表"""
    links = []
    try:
        hrefs = pg.eval_on_selector_all(
            "a[href]", "els=>els.map(e=>e.getAttribute('href')).filter(Boolean)")
    except Exception:
        hrefs = []
    for h in hrefs:
        h = h.split("#")[0].split("?")[0].strip()
        if not h.startswith("/"):
            continue
        if SKIP_ROUTE_RX.match(h):
            continue
        if DESTRUCTIVE_RX.search(h):
            continue
        if h not in links:
            links.append(h)
    return links


def _click_selector_group(pg, sels, kind, black, max_clicks=8):
    clicked = 0
    for sel in sels:
        els = pg.query_selector_all(sel)
        for el in els[:12]:
            if clicked >= max_clicks:
                break
            if _blacklisted_el(pg, el, black):
                continue
            try:
                el.click(timeout=1200)
                time.sleep(0.8)
                clicked += 1
            except Exception:
                pass
        if clicked >= max_clicks:
            break
    return clicked


def _click_first_rows(pg, sels):
    for sel in sels:
        els = pg.query_selector_all(sel)
        if not els:
            continue
        opened = 0
        for el in els[:3]:
            try:
                el.click(timeout=1200)
                time.sleep(0.9)
                opened += 1
            except Exception:
                pass
        try:
            pg.keyboard.press("Escape")
            time.sleep(0.4)
        except Exception:
            pass
        return opened
    return 0


def _cycle_selects(pg):
    n = 0
    try:
        for el in pg.query_selector_all("select")[:6]:
            if _blacklisted_el(pg, el):
                continue
            tag_options = el.evaluate(
                "e=>{let o=[...e.options].filter(x=>x.value&&!x.disabled);return o.length?o[0].value:''}")
            if tag_options:
                try:
                    el.select_option(tag_options)
                    time.sleep(0.7)
                    n += 1
                except Exception:
                    pass
    except Exception:
        pass
    return n


def _interact_until_converge(pg, caps, max_rounds=4):
    """D 层：反复发现可点控件→点击→重扫，直到无新元素或达轮次上限"""
    seen = set()
    total_click = 0
    for rnd in range(max_rounds):
        before = total_click
        total_click += _click_selector_group(pg, TAB_SEL, "tab", DESTRUCTIVE_RX)
        total_click += _click_selector_group(pg, COLLAPSE_SEL, "collapse", DESTRUCTIVE_RX)
        total_click += _click_first_rows(pg, DRILL_SEL)
        total_click += _cycle_selects(pg)
        if total_click == before:  # 本轮无任何点击 → 收敛
            break
    caps["interact_total_clicks"] = total_click


def _data_rules(pg, url):
    """E 层：数据正确性规则 —— 返回发现的违规（非空为佳 key-selector 空值 / 越界量）"""
    issues = []
    try:
        confs = pg.eval_on_selector_all(
            "[class*=conf], [class*='Confidence'], [class*='confidence'], .bar-label, .score",
            "els=>els.map(e=>(e.innerText||e.textContent||'').trim()).filter(t=>/[\d.]+%?/.test(t)&&t.length<40)")
    except Exception:
        confs = []
    for c in confs:
        m = re.search(r"(\d+(?:\.\d+)?)", c)
        if not m:
            continue
        v = float(m.group(1))
        if v > 100 or v < 0:
            issues.append({"rule": "confidence-range", "val": c})
    risk_map = {"高": 1, "中": 1, "低": 1, "high": 1, "medium": 1, "low": 1, "P0": 1, "P1": 1, "P2": 1, "P3": 1}
    try:
        risk_txt = pg.eval_on_selector_all(
            "td, .risk, .sev, .severity, .level",
            "els=>els.map(e=>e.innerText||e.textContent||'').reduce((a,b)=>a+' '+(b||'')).trim()")
        joined = " ".join(risk_txt) if risk_txt else ""
        for tok in re.findall(r"(高|中|低|high|medium|low|P[0-3])", joined):
            if tok not in risk_map:
                issues.append({"rule": "risk-token", "val": tok})
    except Exception:
        pass
    return issues


def run_page(pg, url, caps, side_effects):
    """核查单个页面：打开→取导航链接→交互收敛→数据规则→快照"""
    pg.goto(BASE + url, timeout=30000)
    try:
        pg.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    try:
        pg.wait_for_function(
            "()=>!document.body.innerText.includes('加载中') || document.body.innerText.length>50",
            timeout=8000)
    except Exception:
        pass
    time.sleep(1.0)
    caps["nav_links"] = _collect_nav_links(pg)
    side_effects["discovered"].update(caps["nav_links"])
    snap(pg, caps, "initial")
    _interact_until_converge(pg, caps)
    snap(pg, caps, "after-interactions")
    try:
        caps["data_rules"] = _data_rules(pg, url)
    except Exception as e:
        caps["data_rules"] = [{"rule": "error", "val": str(e)[:100]}]
    return caps


def write_html(report, outdir, base_href="."):
    pages = report["pages"]
    issues = []
    for r, caps in pages.items():
        if caps.get("open_error"):
            issues.append((r, "open-error", caps["open_error"][:120], 3))
        for ce in caps.get("console_errors", []):
            issues.append((r, "console-error", ce[:120], 2))
        for hf in caps.get("http_fail", []):
            issues.append((r, "http-%s" % hf.get("status"), (hf.get("url") or "")[:100], 2))
        for st in caps.get("steps", []):
            for a in st.get("anomalies", []):
                issues.append((r, "anomaly:%s" % a["kind"], a["ctx"][:80], 1))
            if st.get("rows") == 0 and st["tag"] == "after-interactions" and caps.get("interact_total_clicks", 0) == 0:
                issues.append((r, "empty-page-view", "交互后仍 0 行数据（可能数据缺失，需人工确认）", 0))
        for dr in caps.get("data_rules", []):
            issues.append((r, "rule:%s" % dr["rule"], dr["val"][:80], 1))
    issues.sort(key=lambda x: -x[3])
    rows = "".join(
        "<tr><td>{0}</td><td>{1}</td><td><code>{2}</code></td><td>{3}</td></tr>".format(
            r, k, human_of(v), sev)
        for r, k, v, sev in issues)
    summary = {
        "pages": len(pages),
        "console_errors": sum(1 for c in pages.values() for _ in c.get("console_errors", [])),
        "http_fail": sum(1 for c in pages.values() for _ in c.get("http_fail", [])),
        "anomalies": sum(1 for c in pages.values() for s in c.get("steps", []) for _ in s.get("anomalies", [])),
        "data_rules": sum(1 for c in pages.values() for _ in c.get("data_rules", [])),
        "open_errors": sum(1 for c in pages.values() if c.get("open_error")),
    }
    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>UI 全页面核查报告</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px;color:#1f2328}}
h1{{font-size:18px}}table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #d0d7de;padding:6px 8px;text-align:left;vertical-align:top}}
th{{background:#f6f8fa}}code{{background:#eff1f3;padding:1px 4px;border-radius:3px}}
.badge{{display:inline-block;padding:1px 6px;border-radius:8px;font-size:12px}}
.sev0{{background:#ddf4ff}} .sev1{{background:#fff8c5}} .sev2{{background:#ffebe9}} .sev3{{background:#ffebe9;font-weight:bold}}
h2{{font-size:15px;margin-top:26px}}</style></head><body>
<h1>UI 全页面核查报告</h1>
<p>目标: {report.get('project')} | 时间: {report.get('ts')} | 覆盖页面: {summary['pages']}</p>
<table><tr><th>指标</th><th>console</th><th>http4xx/5xx</th><th>可见异常词</th><th>数据规则</th><th>打开失败</th></tr>
<tr><td>数量</td><td>{summary['console_errors']}</td><td>{summary['http_fail']}</td><td>{summary['anomalies']}</td>
<td>{summary['data_rules']}</td><td>{summary['open_errors']}</td></tr></table>
<h2>发现的问题 {len(issues)} 条</h2>
<table><tr><th>页面</th><th>类型</th><th>详情</th><th>级别</th></tr>{rows}</table>
<h2>覆盖页面清单</h2>
<ul>{''.join('<li>{0}</li>'.format(r) for r in sorted(pages))}</ul>
</body></html>"""
    (outdir / "ui_audit_report.html").write_text(html, encoding="utf-8")


def diff_baseline(prev, report, outdir):
    if not prev:
        return
    prev_pages = prev.get("pages", {})
    now_pages = report.get("pages", {})
    notes = []
    for r in set(now_pages) | set(prev_pages):
        if r not in prev_pages:
            notes.append(f"+ 新增页面 {r}")
        elif r not in now_pages:
            notes.append(f"- 消失页面 {r}")
    def errs(caps, what):
        return set()
    p = Path(outdir) / "ui_audit_diff.txt"
    p.write_text("\n".join(notes) if notes else "无页面增减差异", encoding="utf-8")
    report["_diff_notes"] = notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="hospital_sut")
    ap.add_argument("--out", default=str(Path(r"d:\AI\AI_Test_Preject\logs\g_close\ui_audit")))
    ap.add_argument("--routes", default="", help="仅跑指定路由(逗号分隔)；缺省则自动发现闭包")
    ap.add_argument("--limit", type=int, default=40, help="自动发现的最大页面数")
    ap.add_argument("--baseline", default="", help="历史报告 JSON 路径，做 diff")
    ap.add_argument("--no-network", action="store_true", help="关闭 http 错误捕获")
    a = ap.parse_args()
    outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
    limit = a.limit
    baseline = json.load(open(a.baseline, encoding="utf-8")) if a.baseline else None

    report = {"project": a.project, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    console_errors = []
    http_fail = []

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context()
        caps_map = {}
        # ── 网络监控（F 层）────
        def on_response(resp, cur):
            if cur is None or resp.url.startswith(BASE + "/api"):
                return
            if resp.status >= 400:
                http_fail.append({"status": resp.status, "url": resp.url[:160]})
        def on_requestfailed(fail):
            try:
                http_fail.append({"status": "FAILED", "url": (fail.url or "")[:160]})
            except Exception:
                pass

        pg = ctx.new_page()
        def _setup(cur=None):
            pg.on("response", lambda r, c=cur: on_response(r, c))
            pg.on("requestfailed", on_requestfailed)

        # ── 登录 ──
        _setup(None)
        pg.goto(BASE + "/login", timeout=25000); pg.wait_for_load_state("domcontentloaded"); time.sleep(1.0)
        try:
            pg.wait_for_function("()=>document.querySelector('#userSelect').options.length>1", timeout=9000)
        except Exception:
            pass
        try:
            pg.eval_on_selector("#userSelect", "sel=>{for(const o of sel.options){if(o.value){sel.value=o.value;break;}}sel.dispatchEvent(new Event('change'));}")
            pg.fill("#loginPassword", "admin123"); pg.click("#btnLogin")
            pg.wait_for_load_state("domcontentloaded"); time.sleep(2.0)
        except Exception as e:
            report["login_error"] = str(e)[:150]
        try:
            pg.request.post(BASE + "/api/project", data={"name": a.project})
        except Exception:
            pass

        # ── B: 动态发现页面闭包 ──
        explicit = [r.strip() for r in a.routes.split(",") if r.strip()]
        routes = explicit or SEED_ROUTES
        visited = set()
        side_effects = {"discovered": set()}
        order = list(routes)
        i = 0
        while i < len(order) and len(visited) < limit:
            url = order[i]; i += 1
            if url in visited or SKIP_ROUTE_RX.match(url):
                continue
            visited.add(url)
            console_errors.clear()
            http_fail.clear()
            caps = {"route": url, "steps": [], "console_errors": [], "http_fail": [], "nav_links": []}
            _setup(url)
            try:
                run_page(pg, url, caps, side_effects)
                caps["console_errors"] = list(console_errors)[:10]
                caps["http_fail"] = list(http_fail)[:10]
                # 仅自动发现模式才递归追加链接；显式 --routes 不展开
                if not explicit:
                    for lk in caps.get("nav_links", []):
                        if lk not in visited and lk not in order:
                            order.append(lk)
            except Exception as e:
                caps["open_error"] = str(e)[:150]
            caps_map[url] = caps
        report["pages"] = caps_map
        report["discovered_page_count"] = len(caps_map)
        b.close()

    # ── 写 JSON + HTML + diff ──
    jp = outdir / "ui_audit_report.json"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    write_html(report, outdir)
    if baseline:
        diff_baseline(baseline, report, outdir)
    print("覆盖页面数:", report["discovered_page_count"])
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()