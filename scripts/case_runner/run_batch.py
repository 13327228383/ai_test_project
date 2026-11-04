# -*- coding: utf-8 -*-
"""批量真实执行用例集（xlsx → 引擎执行器 → 证据报告）。

用法：
  python scripts/case_runner/run_batch.py --domain 断言指令轨 --limit 50
  python scripts/case_runner/run_batch.py --domain 操作指令轨 --offset 0 --limit 100
  python scripts/case_runner/run_batch.py --all

输出：logs/case_runner/<domain>_<ts>.json（逐条证据，可复核）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter

import openpyxl

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.case_runner._env import CaseEnv, SUT_BASE
from scripts.case_runner._run import exec_one

# DB 轨连接配置（否则 DB 类断言全部走「连接失败→跳过」，无法真实验证）
os.environ.setdefault("TARGET_DB_TYPE", "mysql")
os.environ.setdefault("TARGET_DB_HOST", "127.0.0.1")
os.environ.setdefault("TARGET_DB_PORT", "3306")
os.environ.setdefault("TARGET_DB_NAME", "ai_test_engine")
# ★ 用例集含故障注入/服务桩动作（network_fault/service_mock），引擎默认门控关闭——
#   验证这两类能力必须显式开启（属测试环境配置，非引擎代码变更）
os.environ.setdefault("NETWORK_FAULT_ENABLED", "1")
os.environ.setdefault("SERVICE_MOCK_ENABLED", "1")

XLSX = os.path.join(_ROOT, "docs", "引擎端到端测试用例集.xlsx")
OUT_DIR = os.path.join(_ROOT, "logs", "case_runner")


def load_cases(domain=None, limit=None, offset=0, case_ids=None, exclude=None):
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb["全量用例"]
    rows = list(ws.iter_rows(min_row=1, values_only=True))
    hdr = [str(c) if c else "" for c in rows[0]]
    data = [dict(zip(hdr, r)) for r in rows[1:] if r[0] is not None]
    if domain:
        doms = {d.strip() for d in str(domain).split(",") if d.strip()}
        data = [d for d in data if str(d.get("功能域", "")) in doms]
    if exclude:
        ex = {d.strip() for d in str(exclude).split(",") if d.strip()}
        data = [d for d in data if str(d.get("功能域", "")) not in ex]
    if case_ids:
        idset = {str(i) for i in case_ids}
        data = [d for d in data if str(d.get("用例ID")) in idset]
    data = data[offset:]
    if limit:
        data = data[:limit]
    return data


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default=None, help="功能域过滤")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--ids", default=None, help="逗号分隔用例ID")
    ap.add_argument("--exclude", default=None, help="排除的功能域（逗号分隔）")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    ids = args.ids.split(",") if args.ids else None
    cases = load_cases(args.domain, args.limit, args.offset, ids, args.exclude)
    print(f"[load] 待执行用例数 = {len(cases)}" + (f" (domain={args.domain})" if args.domain else ""))

    env = CaseEnv(headless=not args.headed)
    await env.start()
    print(f"[env] browser ready, logged_in={env.logged_in} err={env.login_error}")
    if not env.logged_in:
        print("[env] 登录失败，终止（禁止无登录态假执行）")
        await env.close()
        return 2

    await env.goto(SUT_BASE + "/patients")
    print("[env] page =", env.page.url if env.page else None)

    results = []
    t0 = time.time()
    for i, c in enumerate(cases, 1):
        try:
            r = await exec_one(env, c)
        except Exception as e:
            r = {
                "case_id": c.get("用例ID"),
                "功能域": c.get("功能域"),
                "变化点": c.get("变化点"),
                "verdict": "ERROR",
                "error": f"runner crash: {type(e).__name__}: {e}",
            }
        results.append(r)
        if i % 25 == 0 or r.get("verdict") in ("FAIL", "ERROR", "NO_HANDLER"):
            print(
                f"  [{i}/{len(cases)}] id={r.get('case_id')} {r.get('verdict'):<11} "
                f"tool={r.get('tool')} expect={r.get('expect')} actual={r.get('actual')} "
                f"| {str(r.get('error') or r.get('content') or '')[:90]}"
            )

    await env.close()

    os.makedirs(OUT_DIR, exist_ok=True)
    tag = args.tag or (args.domain or "all")
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(OUT_DIR, f"{tag}_{ts}.json")
    cnt = Counter(r.get("verdict") for r in results)
    payload = {
        "domain": args.domain,
        "total": len(results),
        "summary": dict(cnt),
        "elapsed_s": round(time.time() - t0, 1),
        "sut": SUT_BASE,
        "results": results,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print()
    print("=" * 70)
    print(f"执行完成 {len(results)} 条，用时 {payload['elapsed_s']}s")
    for k, v in cnt.most_common():
        print(f"  {k:<12} {v}")
    print("报告:", out)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
