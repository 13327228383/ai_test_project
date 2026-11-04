# -*- coding: utf-8 -*-
"""从 MySQL ``executions`` 表生成可复算的验证报告（逐用例 view）。

★ 2026-09-04 重建：全仓审计发现本脚本与 ``e2e-test-report-2026-08-10/verification_report.*``
  从工作区丢失，但 AGENTS.md 与项目记忆一直以其作为「112/113 (99.1%)」通过率的可复算来源。
  本文件是**根因重建** —— 复刻文档描述的能力（从 executions 直查、逐 case 结果、
  输出 JSON+HTML），数据源零依赖引擎业务，任何时候可重跑复算。

用法:
    python scripts/maintenance/generate_verification_report.py \
        [--project PROJECT] [--run RUN_ID] [--out-dir DIR] [--limit N]

默认行为：不加过滤时汇总全部 executions；输出 JSON + HTML 到 out-dir（默认
e2e-test-report-2026-08-10/）。判定口径与引擎一致：
    PASS/FAIL/SKIP/ERROR/FLAKY 严格区分，通过率 = PASS / 非SKIP总数，
    附平均置信度（confidence 列存在时）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# 允许独立脚本运行
_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _connect():
    from src.core.db_manager import get_connection, release_connection

    conn = get_connection()
    return conn, release_connection


def list_columns(cur) -> set[str]:
    cols = set()
    if cur and cur.description:
        for d in cur.description:
            cols.add(d[0])
    return cols


def fetch(project: str | None, run: str | None, limit: int | None):
    conn, rel = _connect()
    try:
        q = "SELECT * FROM executions WHERE 1=1"
        params = []
        if project:
            q += " AND project=%s"
            params.append(project)
        if run:
            q += " AND run_id=%s"
            params.append(run)
        q += " ORDER BY id"
        if limit:
            q += " LIMIT %s"
            params.append(limit)
        cur = conn.execute(q, tuple(params))
        cols = list_columns(cur)
        rows = []
        for r in cur.fetchall() if cur else ():
            if isinstance(r, dict):
                rows.append(r)
            elif hasattr(r, "keys"):  # DAI Row / 类 dict
                rows.append({k: r[k] for k in r.keys()})
            else:
                rows.append(dict(r))
        return cols, rows
    finally:
        rel(conn)


def _summarize(cols: set[str], rows: list[dict]) -> dict:
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0, "ERROR": 0, "FLAKY": 0}
    conf_sum = 0.0
    for r in rows:
        st = (r.get("status") or "SKIP").upper()
        counts[st] = counts.get(st, 0) + 1
        conf = r.get("confidence")
        if "confidence" in cols and isinstance(conf, (int, float)):
            conf_sum += float(conf)
    non_skip = counts["PASS"] + counts["FAIL"] + counts["ERROR"] + counts["FLAKY"]
    rate = (counts["PASS"] / non_skip) if non_skip else None
    avg_conf = (conf_sum / len(rows)) if rows and "confidence" in cols else None
    return {
        "total": len(rows),
        "counts": counts,
        "non_skip": non_skip,
        "pass_rate_%": (round(rate * 100, 1) if rate is not None else None),
        "avg_confidence": (round(avg_conf, 4) if avg_conf is not None else None),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="从 executions 表生成验证报告")
    ap.add_argument("--project", default=None, help="仅统计指定项目")
    ap.add_argument("--run", default=None, help="仅统计指定 run_id")
    ap.add_argument(
        "--out-dir", default="e2e-test-report-2026-08-10", help="输出目录"
    )
    ap.add_argument("--limit", type=int, default=None, help="最多取前 N 条")
    a = ap.parse_args()

    cols, rows = fetch(a.project, a.run, a.limit)
    if not rows:
        print("无 executions 记录（project=%r run=%r）" % (a.project, a.run),
              file=sys.stderr)
        return 1
    summary = _summarize(cols, rows)
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "filter": {"project": a.project, "run": a.run, "limit": a.limit},
        "summary": summary,
        "cases": rows,
    }
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jp = out_dir / "verification_report.json"
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                  encoding="utf-8")
    hp = out_dir / "verification_report.html"
    hp.write_text(_render_html(summary, rows), encoding="utf-8")
    print("已生成: %s" % jp)
    print("已生成: %s" % hp)
    print(
        "summary: total=%d PASS=%d FAIL=%d SKIP=%d ERROR=%d 通过率=%s 平均置信度=%s"
        % (
            summary["total"], summary["counts"]["PASS"], summary["counts"]["FAIL"],
            summary["counts"]["SKIP"], summary["counts"]["ERROR"],
            summary["pass_rate_%"], summary["avg_confidence"],
        )
    )
    return 0


def _render_html(summary: dict, rows: list[dict]) -> str:
    c = summary["counts"]
    rate = summary["pass_rate_%"]
    rows_html = "\n".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
        "<td>%s</td><td>%s</td><td>%s</td></tr>" % (
            html(r.get("case_id", "")), html(r.get("status", "")),
            r.get("confidence"), html(r.get("expected", "")),
            html(r.get("actual", "")), html(r.get("failure_reason", "")),
            html(r.get("failure_category", "")), html(r.get("executed_at", "")),
        )
        for r in rows
    ) or "<tr><td colspan=8>无数据</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>验证报告</title>
<style>
body{{font-family:system-ui,Arial;margin:20px;background:#0f172a;color:#e2e8f0}}
.card{{background:#1e293b;border-radius:10px;padding:18px;margin-bottom:18px}}
h1,h2{{color:#38bdf8}} table{{border-collapse:collapse;width:100%;font-size:12px}}
th,td{{border:1px solid #334155;padding:6px 8px;text-align:left;vertical-align:top}}
.pass{{color:#10b981;font-weight:700}} .fail{{color:#f87171;font-weight:700}}
.skip{{color:#94a3b8}} th{{background:#334155}}
</style></head><body>
<h1>测试引擎验证报告（executions 直查可复算）</h1>
<div class="card">生成时间: {summary.get("generated_at", "")}
<p>总用例 <b>{summary["total"]}</b> | PASS <span class="pass">{c["PASS"]}</span> |
FAIL <span class="fail">{c["FAIL"]}</span> | SKIP <span class="skip">{c["SKIP"]}</span> |
ERROR <span class="fail">{c["ERROR"]}</span> | FLAKY {c["FLAKY"]}
<br/>通过率 <b>{rate}%</b> | 平均置信度 <b>{summary.get("avg_confidence")}</b></p></div>
<div class="card"><table>
<tr><th>用例ID</th><th>状态</th><th>置信度</th><th>预期</th><th>实际</th>
<th>失败原因</th><th>失败分类</th><th>执行时间</th></tr>
{rows_html}
</table></div>
</body></html>"""


def html(s) -> str:
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    sys.exit(main())