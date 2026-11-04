# -*- coding: utf-8 -*-
"""诊断 6：TC-REQ-002-I01 的完整失败原因（检查项全 PASS 却判 FAIL）。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.db_manager import get_connection, release_connection  # noqa: E402


def main():
    conn = get_connection()
    try:
        for cid in ("TC-REQ-002-I01", "TC-REQ-001-I02", "TC-REQ-001-I01"):
            r = conn.execute(
                "SELECT case_id, status, confidence, verification_track, "
                "failure_category, expected, actual, details, failure_reason, "
                "confidence_factors, executed_at "
                "FROM executions WHERE project='accept_sut_0923' AND case_id=%s "
                "ORDER BY executed_at DESC LIMIT 1",
                (cid,),
            ).fetchone()
            if not r:
                print(f"\n### {cid}: 无记录")
                continue
            print("\n" + "=" * 90)
            print(f"### {cid}  status={r['status']} conf={r['confidence']} "
                  f"track={r['verification_track']} cat={r['failure_category']} "
                  f"@{r['executed_at']}")
            print("=" * 90)
            for k in ("expected", "actual", "details", "failure_reason",
                      "confidence_factors"):
                v = r[k]
                print(f"\n--- {k} ---")
                print(str(v)[:1500] if v else "(空)")
    finally:
        release_connection(conn)


if __name__ == "__main__":
    main()