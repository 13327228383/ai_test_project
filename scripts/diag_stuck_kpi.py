# -*- coding: utf-8 -*-
"""诊断：Dashboard KPI 卡在 91/267 / 358 已执行的取数根因。

只读，不改任何数据。输出：
  1) executions 按 project+version_id+status 分组分布
  2) run_summary 全部行（KPI 首选数据源）
  3) 各 project 的 active version_id
  4) 模拟 _real_stats 的 passed/failed 计算（含 is_fake_pass 降级）
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.db_manager import get_connection, release_connection  # noqa: E402


def main():
    conn = get_connection()
    try:
        print("=" * 90)
        print("[1] executions 按 project + version_id + status 分组")
        print("=" * 90)
        rows = conn.execute(
            "SELECT project, version_id, status, COUNT(*) AS n "
            "FROM executions GROUP BY project, version_id, status "
            "ORDER BY project, version_id, n DESC"
        ).fetchall()
        cur = None
        for r in rows:
            key = (r["project"], r["version_id"])
            if key != cur:
                print(f"\n--- project={r['project']} version_id={r['version_id']} ---")
                cur = key
            print(f"    {str(r['status']):>22} : {r['n']}")

        print("\n" + "=" * 90)
        print("[2] executions 按 project + version_id 总计")
        print("=" * 90)
        rows = conn.execute(
            "SELECT project, version_id, COUNT(*) AS n, "
            "COUNT(DISTINCT case_id) AS distinct_cases, "
            "MIN(executed_at) AS first_at, MAX(executed_at) AS last_at "
            "FROM executions GROUP BY project, version_id ORDER BY n DESC"
        ).fetchall()
        for r in rows:
            print(
                f"  {r['project']:<28} v={str(r['version_id']):<8} "
                f"rows={r['n']:<5} cases={r['distinct_cases']:<5} "
                f"{r['first_at']} -> {r['last_at']}"
            )

        print("\n" + "=" * 90)
        print("[3] run_summary 全部行")
        print("=" * 90)
        try:
            rows = conn.execute(
                "SELECT project, version_id, total_passed, total_failed, "
                "total_cases, pass_rate, created_at "
                "FROM run_summary ORDER BY created_at DESC LIMIT 40"
            ).fetchall()
            if not rows:
                print("  (空表 —— KPI 将走 executions fallback)")
            for r in rows:
                print(
                    f"  {r['project']:<28} v={str(r['version_id']):<8} "
                    f"P={r['total_passed']} F={r['total_failed']} "
                    f"T={r['total_cases']} rate={r['pass_rate']} @{r['created_at']}"
                )
        except Exception as e:
            print(f"  run_summary 查询失败: {e}")

        print("\n" + "=" * 90)
        print("[4] project_versions（active 版本）")
        print("=" * 90)
        try:
            rows = conn.execute(
                "SELECT project, version_id, version_name, is_active, created_at "
                "FROM project_versions ORDER BY project, version_id DESC LIMIT 40"
            ).fetchall()
            if not rows:
                print("  (空表)")
            for r in rows:
                print(
                    f"  {r['project']:<28} v={str(r['version_id']):<8} "
                    f"name={r['version_name']} active={r['is_active']} @{r['created_at']}"
                )
        except Exception as e:
            print(f"  project_versions 查询失败: {e}")

        print("\n" + "=" * 90)
        print("[5] 模拟 _real_stats：per project+version 的 PASS/FAIL(含假PASS降级)")
        print("=" * 90)
        from src.users.audit import is_fake_pass

        rows = conn.execute(
            "SELECT e.case_id, e.status, e.confidence, e.expected, e.actual, "
            "e.details, e.project, e.version_id FROM executions e "
            "INNER JOIN (SELECT case_id, project, version_id, MAX(executed_at) AS max_at "
            "            FROM executions GROUP BY case_id, project, version_id) latest "
            "ON e.case_id=latest.case_id AND e.project=latest.project "
            "AND e.version_id=latest.version_id AND e.executed_at=latest.max_at"
        ).fetchall()
        agg: dict = {}
        fake_cnt = 0
        for r in rows:
            raw = (r["status"] or "").strip().upper()
            smap = {
                "PASSED": "PASS", "FAILED": "FAIL", "SKIPPED": "SKIP",
                "FLAKY": "FLAKY", "ERROR": "FAIL", "PRODUCT_BUG": "FAIL",
                "HARNESS_ERROR": "FAIL", "ENVIRONMENT_ERROR": "FAIL",
                "DATA_ERROR": "FAIL", "PASS_WITH_SLOW_BUG": "PASS",
            }
            mapped = smap.get(raw, raw)
            if mapped == "PASS" and is_fake_pass(
                r["confidence"], r["expected"], r["actual"], r["details"]
            ):
                mapped = "FAIL"
                fake_cnt += 1
            k = (r["project"], str(r["version_id"]))
            agg.setdefault(k, {"PASS": 0, "FAIL": 0, "SKIP": 0, "OTHER": 0})
            agg[k][mapped if mapped in ("PASS", "FAIL", "SKIP") else "OTHER"] += 1
        for k, v in sorted(agg.items(), key=lambda x: -(x[1]["PASS"] + x[1]["FAIL"])):
            print(
                f"  {k[0]:<28} v={k[1]:<8} PASS={v['PASS']:<5} FAIL={v['FAIL']:<5} "
                f"SKIP={v['SKIP']:<5} OTHER={v['OTHER']:<5} "
                f"executed={v['PASS'] + v['FAIL']}"
            )
        print(f"\n  假 PASS 被降级为 FAIL 的行数: {fake_cnt}")
    finally:
        release_connection(conn)


if __name__ == "__main__":
    main()