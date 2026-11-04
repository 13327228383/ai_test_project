# -*- coding: utf-8 -*-
"""诊断 8：复现 check_guard 极性过滤为何未剔除正向用例的 field_error_visible_check。"""
import json
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.db_manager import get_connection, release_connection  # noqa: E402
from src.pipeline.step3._check_guard import (  # noqa: E402
    sanitize_case_checks,
    _NEGATIVE_SEMANTIC_RE,
    _EXECUTABLE_CHECK_TYPES,
)
from src.pipeline.step3._rule_extractor_keywords import expects_error_feedback  # noqa: E402


def main():
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT case_id, title, payload_json, expected_result, expected_outcome "
            "FROM test_cases WHERE project='accept_sut_0923' AND case_id=%s",
            ("TC-REQ-002-I01",),
        ).fetchone()
        payload = json.loads(r["payload_json"])
        print("payload 顶层键:", list(payload.keys()))

        case = dict(payload)
        case["case_id"] = r["case_id"]
        case["expected_result"] = r["expected_result"]

        _explore_task = case.get("_explore_task") if isinstance(case.get("_explore_task"), dict) else {}
        _probe_text = " ".join(
            str(case.get(k) or "")
            for k in ("title", "operation", "description",
                      "expected_result", "expected_feedback")
        ) + " " + str(_explore_task.get("expected_feedback") or "")
        print("\n_probe_text =", repr(_probe_text))
        m = _NEGATIVE_SEMANTIC_RE.search(_probe_text)
        print("负向正则命中 =", repr(m.group(0)) if m else None)
        print("expects_error_feedback(_probe_text) =", expects_error_feedback(_probe_text))

        print("\n--- 各字段单独测正则 ---")
        for k in ("title", "operation", "description", "expected_result",
                  "expected_feedback"):
            v = str(case.get(k) or "")
            mm = _NEGATIVE_SEMANTIC_RE.search(v)
            print(f"  {k:<20} 命中={repr(mm.group(0)) if mm else None:<10} val={v[:70]!r}")

        print("\n--- 执行 sanitize_case_checks 前后 checks 数量 ---")
        before = len(case.get("checks") or [])
        for c in case.get("checks") or []:
            ct = (c.get("check") or {}).get("type")
            print(f"    before: {ct}")
        sanitize_case_checks(case)
        after = len(case.get("checks") or [])
        print(f"  before={before} after={after}")
        for c in case.get("checks") or []:
            ct = (c.get("check") or {}).get("type")
            print(f"    after : {ct}")
    finally:
        release_connection(conn)


if __name__ == "__main__":
    main()