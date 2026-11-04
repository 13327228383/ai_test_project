# -*- coding: utf-8 -*-
"""诊断 2：为何数据"卡住" —— 检查 run_logs 是否有新 run、active 版本是否漂移。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.db_manager import get_connection, release_connection  # noqa: E402


def _try(conn, label, sql, params=()):
    print(f"\n--- {label} ---")
    try:
        rows = conn.execute(sql, params).fetchall()
        if not rows:
            print("  (空)")
        for r in rows:
            print("  " + " | ".join(f"{k}={r[k]}" for k in r.keys()))
    except Exception as e:
        print(f"  查询失败: {e}")


def main():
    conn = get_connection()
    try:
        print("=" * 90)
        print("[1] run_logs 最近 60 条（RUN_START / RUN_END）")
        print("=" * 90)
        _try(
            conn,
            "recent run_logs",
            "SELECT id, project, run_id, level, message, created_at "
            "FROM run_logs ORDER BY id DESC LIMIT 60",
        )

        print("\n" + "=" * 90)
        print("[2] project_versions 表结构 + 内容")
        print("=" * 90)
        _try(
            conn,
            "columns",
            "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA='ai_test_engine' AND TABLE_NAME='project_versions'",
        )
        _try(
            conn,
            "rows",
            "SELECT * FROM project_versions ORDER BY 1 DESC LIMIT 30",
        )

        print("\n" + "=" * 90)
        print("[3] 最近 2 天（>=2026-09-24）executions 写入")
        print("=" * 90)
        _try(
            conn,
            "recent executions",
            "SELECT project, version_id, status, COUNT(*) n, MAX(executed_at) last_at "
            "FROM executions WHERE executed_at >= '2026-09-24' "
            "GROUP BY project, version_id, status ORDER BY last_at DESC",
        )

        print("\n" + "=" * 90)
        print("[4] accept_sut_0923 的 case 总数（test_cases 表）")
        print("=" * 90)
        _try(
            conn,
            "test_cases count",
            "SELECT project, COUNT(*) n, COUNT(DISTINCT case_id) dc "
            "FROM test_cases GROUP BY project ORDER BY n DESC LIMIT 20",
        )

        print("\n" + "=" * 90)
        print("[5] accept_sut_0923 executions 样本：FAIL 的 failure_reason 分布")
        print("=" * 90)
        _try(
            conn,
            "FAIL reasons",
            "SELECT LEFT(COALESCE(failure_reason,''), 90) reason, COUNT(*) n "
            "FROM executions WHERE project='accept_sut_0923' AND version_id=3386 "
            "AND status='FAIL' GROUP BY reason ORDER BY n DESC LIMIT 30",
        )
        _try(
            conn,
            "ERROR reasons",
            "SELECT LEFT(COALESCE(failure_reason,''), 90) reason, COUNT(*) n "
            "FROM executions WHERE project='accept_sut_0923' AND version_id=3386 "
            "AND status='ERROR' GROUP BY reason ORDER BY n DESC LIMIT 30",
        )
    finally:
        release_connection(conn)


if __name__ == "__main__":
    main()