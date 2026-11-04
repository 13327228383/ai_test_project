# -*- coding: utf-8 -*-
"""诊断 3：run_logs 最近活动 + 兼容/易用用例定义 + 失败是否同源。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.db_manager import get_connection, release_connection  # noqa: E402


def main():
    conn = get_connection()
    try:
        print("=" * 90)
        print("[1] run_logs 表结构")
        print("=" * 90)
        cols = conn.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA='ai_test_engine' AND TABLE_NAME='run_logs' "
            "ORDER BY ORDINAL_POSITION"
        ).fetchall()
        names = [c["COLUMN_NAME"] for c in cols]
        print("  " + ", ".join(names))

        print("\n" + "=" * 90)
        print("[2] run_logs 最近 40 条（所有项目）")
        print("=" * 90)
        try:
            rows = conn.execute(
                "SELECT * FROM run_logs ORDER BY id DESC LIMIT 40"
            ).fetchall()
            for r in rows:
                d = {k: r[k] for k in r.keys()}
                print("  " + str(d)[:300])
        except Exception as e:
            print(f"  失败: {e}")

        print("\n" + "=" * 90)
        print("[3] accept_sut_0923：按 case_type 统计 executions 状态")
        print("=" * 90)
        rows = conn.execute(
            "SELECT t.case_type, e.status, COUNT(*) n "
            "FROM executions e JOIN test_cases t "
            "  ON e.case_id=t.case_id AND t.project=e.project "
            "WHERE e.project='accept_sut_0923' AND e.version_id=3386 "
            "GROUP BY t.case_type, e.status ORDER BY t.case_type, n DESC"
        ).fetchall()
        cur = None
        for r in rows:
            if r["case_type"] != cur:
                print(f"\n  --- case_type={r['case_type']} ---")
                cur = r["case_type"]
            print(f"      {r['status']:>8} : {r['n']}")

        print("\n" + "=" * 90)
        print("[4] 失败用例的 check 类型分布（从 failure_reason 提取）")
        print("=" * 90)
        rows = conn.execute(
            "SELECT "
            "  CASE "
            "    WHEN failure_reason LIKE '%%viewport_rendering_check%%' THEN 'viewport_rendering_check' "
            "    WHEN failure_reason LIKE '%%click_target_size_check%%' THEN 'click_target_size_check' "
            "    WHEN failure_reason LIKE '%%field_error_visible_check%%' THEN 'field_error_visible_check' "
            "    WHEN failure_reason LIKE '%%semantic_check%%' THEN 'semantic_check' "
            "    WHEN failure_reason LIKE '%%Agent执行失败%%' THEN 'AGENT_EXEC_ERROR' "
            "    WHEN failure_reason LIKE '%%假 PASS%%' THEN 'FAKE_PASS_DOWNGRADE' "
            "    ELSE 'OTHER' END AS kind, COUNT(*) n "
            "FROM executions WHERE project='accept_sut_0923' AND version_id=3386 "
            "AND status IN ('FAIL','ERROR') GROUP BY kind ORDER BY n DESC"
        ).fetchall()
        for r in rows:
            print(f"  {r['kind']:<28} : {r['n']}")

        print("\n" + "=" * 90)
        print("[5] 样本：viewport/click 失败用例的 case_id + case_type + title")
        print("=" * 90)
        rows = conn.execute(
            "SELECT e.case_id, t.case_type, LEFT(t.title, 40) title, LEFT(e.failure_reason,60) fr "
            "FROM executions e JOIN test_cases t "
            "  ON e.case_id=t.case_id AND t.project=e.project "
            "WHERE e.project='accept_sut_0923' AND e.version_id=3386 "
            "AND (e.failure_reason LIKE '%%viewport_rendering_check%%' "
            "  OR e.failure_reason LIKE '%%click_target_size_check%%') "
            "ORDER BY t.case_type, e.case_id LIMIT 20"
        ).fetchall()
        for r in rows:
            print(f"  {r['case_id']:<26} {r['case_type']:<6} {r['title']}")
    finally:
        release_connection(conn)


if __name__ == "__main__":
    main()