# -*- coding: utf-8 -*-
"""诊断 7：检查被合并用例的 checks 构成（矛盾断言）。"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.db_manager import get_connection, release_connection  # noqa: E402


def main():
    conn = get_connection()
    try:
        cols = conn.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA='ai_test_engine' AND TABLE_NAME='test_cases' "
            "ORDER BY ORDINAL_POSITION"
        ).fetchall()
        names = [c["COLUMN_NAME"] for c in cols]
        print("test_cases 列: " + ", ".join(names))

        for cid in ("TC-REQ-002-I01", "TC-REQ-001-I01", "TC-REQ-001-I02"):
            r = conn.execute(
                "SELECT * FROM test_cases WHERE project='accept_sut_0923' AND case_id=%s "
                "ORDER BY updated_at DESC LIMIT 1",
                (cid,),
            ).fetchone()
            print("\n" + "=" * 90)
            if not r:
                print(f"### {cid}: 无记录")
                continue
            print(f"### {cid}")
            print("=" * 90)
            for k in r.keys():
                if k in ("payload_json", "case_actions_json"):
                    continue
                print(f"  {k} = {str(r[k])[:120]}")
            for k in ("case_actions_json", "payload_json"):
                v = r.get(k)
                print(f"\n--- {k} ---")
                if not v:
                    print("  (空)")
                    continue
                try:
                    obj = json.loads(v) if isinstance(v, str) else v
                    print(json.dumps(obj, ensure_ascii=False, indent=2)[:2500])
                except Exception as e:
                    print(f"  解析失败({e}): {str(v)[:800]}")
    finally:
        release_connection(conn)


if __name__ == "__main__":
    main()