# -*- coding: utf-8 -*-
"""诊断 4：Step4 停滞期（09-25 凌晨~中午）run_logs 尾部 + PRE_GATE 行内容。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.db_manager import get_connection, release_connection  # noqa: E402


def main():
    conn = get_connection()
    try:
        print("=" * 90)
        print("[1] PRE_GATE / PRE_GATE_BLOCKED 行（全项目）")
        print("=" * 90)
        rows = conn.execute(
            "SELECT id, project, version_id, case_id, status, "
            "LEFT(COALESCE(failure_reason,''),120) fr, executed_at "
            "FROM executions WHERE status LIKE 'PRE_GATE%%' ORDER BY id DESC LIMIT 20"
        ).fetchall()
        for r in rows:
            print(f"  id={r['id']} {r['project']} v={r['version_id']} "
                  f"case={r['case_id']} status={r['status']} @{r['executed_at']}")
            print(f"      fr={r['fr']}")

        print("\n" + "=" * 90)
        print("[2] 09-25 03:00~12:00 run_logs（accept_sut_final step4）尾部 40 条")
        print("=" * 90)
        rows = conn.execute(
            "SELECT id, step_num, log_level, LEFT(message,160) m, created_at "
            "FROM run_logs WHERE project='accept_sut_final' "
            "AND created_at BETWEEN '2026-09-25 03:00:00' AND '2026-09-25 12:00:00' "
            "ORDER BY id DESC LIMIT 40"
        ).fetchall()
        if not rows:
            print("  (无)")
        for r in rows:
            print(f"  #{r['id']} step{r['step_num']} {r['log_level']} @{r['created_at']}")
            print(f"      {r['m']}")

        print("\n" + "=" * 90)
        print("[3] accept_sut_final executions 全部行")
        print("=" * 90)
        rows = conn.execute(
            "SELECT id, case_id, status, confidence, "
            "LEFT(COALESCE(failure_reason,''),150) fr, executed_at "
            "FROM executions WHERE project='accept_sut_final' ORDER BY id"
        ).fetchall()
        for r in rows:
            print(f"  id={r['id']} {r['case_id']:<22} {r['status']:<10} "
                  f"conf={r['confidence']} @{r['executed_at']}")
            if r["fr"]:
                print(f"      {r['fr']}")
    finally:
        release_connection(conn)


if __name__ == "__main__":
    main()