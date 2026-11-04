# -*- coding: utf-8 -*-
"""分析 ui_audit_report.json，汇总各页问题清单"""
import json, sys
from pathlib import Path

p = Path(sys.argv[1] if len(sys.argv) > 1 else r"d:\AI\AI_Test_Preject\logs\g_close\ui_audit\ui_audit_report.json")
d = json.load(open(p, encoding="utf-8"))
pages = d.get("pages", {})
print("=== 报告时间:", d.get("ts"), "| 覆盖页面:", d.get("discovered_page_count", len(pages)), "===")

stats = {"console": 0, "http": 0, "anomaly": 0, "rules": 0, "open": 0, "zero_data": 0}
for route, c in pages.items():
    rows_init = next((s.get("rows") for s in c.get("steps", []) if s["tag"] == "initial"), -1)
    rows_aft = next((s.get("rows") for s in c.get("steps", []) if s["tag"] == "after-interactions"), -1)
    clicks = c.get("interact_total_clicks", 0)
    # 该页是否零数据且无任何交互（疑似空洞）
    if rows_init == 0 and rows_aft == 0 and clicks == 0 and not c.get("nav_links"):
        pass
    ce = c.get("console_errors", [])
    hf = c.get("http_fail", [])
    op = c.get("open_error")
    anom = [a for s in c.get("steps", []) for a in s.get("anomalies", [])]
    dl = c.get("data_rules", [])
    stats["console"] += len(ce); stats["http"] += len(hf); stats["anomaly"] += len(anom)
    stats["rules"] += len(dl); stats["open"] += (1 if op else 0)
    flags = []
    if op: flags.append("OPEN_ERR")
    if ce: flags.append("console:%d" % len(ce))
    if hf: flags.append("http:%s" % hf[0].get("status"))
    if anom: flags.append("anomaly:%s" % anom[0]["kind"])
    if dl: flags.append("rule:%s" % dl[0]["rule"])
    print("%-28s rows=%3s->%-3s click=%2d %s" % (route, rows_init, rows_aft, clicks, " ".join(flags)))

print("\n=== 汇总 ===")
print("console_errors:", stats["console"])
print("http_fail:", stats["http"])
print("anomalies:", stats["anomaly"])
print("data_rules:", stats["rules"])
print("open_errors:", stats["open"])