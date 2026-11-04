# -*- coding: utf-8 -*-
"""诊断 5：重跑前置条件（AI 端点 / 用例审核状态 / 可重跑子集）。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.db_manager import get_connection, release_connection  # noqa: E402

PROJECT = "accept_sut_0923"


def main():
    conn = get_connection()
    try:
        print("=" * 90)
        print("[1] user_credentials 的 API_BASE_URL / 模型端点")
        print("=" * 90)
        try:
            rows = conn.execute(
                "SELECT user_id, API_BASE_URL, MODEL_NAME, PROVIDER "
                "FROM user_credentials ORDER BY user_id LIMIT 20"
            ).fetchall()
            for r in rows:
                print(f"  {r['user_id']:<12} base={r['API_BASE_URL']} "
                      f"model={r['MODEL_NAME']} provider={r['PROVIDER']}")
        except Exception as e:
            print(f"  失败: {e}")
            cols = conn.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA='ai_test_engine' AND TABLE_NAME='user_credentials'"
            ).fetchall()
            print("  列: " + ", ".join(c["COLUMN_NAME"] for c in cols))

        print("\n" + "=" * 90)
        print(f"[2] {PROJECT} 用例审核状态（case_meta.review_status）")
        print("=" * 90)
        try:
            rows = conn.execute(
                "SELECT review_status, COUNT(*) n FROM case_meta "
                "WHERE project=%s GROUP BY review_status",
                (PROJECT,),
            ).fetchall()
            for r in rows:
                print(f"  {r['review_status']:<20} : {r['n']}")
        except Exception as e:
            print(f"  失败: {e}")

        print("\n" + "=" * 90)
        print(f"[3] {PROJECT} 需求审核状态")
        print("=" * 90)
        try:
            rows = conn.execute(
                "SELECT review_status, COUNT(*) n FROM requirements "
                "WHERE project=%s GROUP BY review_status",
                (PROJECT,),
            ).fetchall()
            for r in rows:
                print(f"  {r['review_status']:<20} : {r['n']}")
        except Exception as e:
            print(f"  失败: {e}")

        print("\n" + "=" * 90)
        print(f"[4] {PROJECT} 重跑子集候选：历史上 FAIL/ERROR 的功能类用例（取 6 条）")
        print("=" * 90)
        rows = conn.execute(
            "SELECT e.case_id, t.case_type, e.status, LEFT(t.title,36) title "
            "FROM executions e JOIN test_cases t "
            "  ON e.case_id=t.case_id AND t.project=e.project "
            "WHERE e.project=%s AND e.version_id=3386 "
            "AND e.status IN ('FAIL','ERROR') AND t.case_type='功能' "
            "ORDER BY e.case_id LIMIT 6",
            (PROJECT,),
        ).fetchall()
        ids = []
        for r in rows:
            ids.append(r["case_id"])
            print(f"  {r['case_id']:<22} {r['case_type']:<4} {r['status']:<7} {r['title']}")
        print("\n  CASE_IDS=" + ",".join(ids))

        print("\n" + "=" * 90)
        print(f"[5] {PROJECT} 历史 PASS 的功能类用例（对照组，取 4 条）")
        print("=" * 90)
        rows = conn.execute(
            "SELECT e.case_id, t.case_type, e.status, LEFT(t.title,36) title "
            "FROM executions e JOIN test_cases t "
            "  ON e.case_id=t.case_id AND t.project=e.project "
            "WHERE e.project=%s AND e.version_id=3386 "
            "AND e.status='PASS' AND t.case_type='功能' "
            "ORDER BY e.case_id LIMIT 4",
            (PROJECT,),
        ).fetchall()
        pids = []
        for r in rows:
            pids.append(r["case_id"])
            print(f"  {r['case_id']:<22} {r['case_type']:<4} {r['status']:<7} {r['title']}")
        print("\n  PASS_IDS=" + ",".join(pids))
    finally:
        release_connection(conn)


if __name__ == "__main__":
    main()