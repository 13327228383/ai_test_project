# -*- coding: utf-8 -*-
"""RC-S 存量脏数据清理：删除用例 test_method.verifications 中残留的「字段约束」验证点。

背景（根因）
-----------
字段约束型需求（"最多40字符"/"非负整数"/"active/maintenance/retired 下拉"）的期望
性质是**控件 DOM 约束**，页面上不存在描述这些规则的文字。断言层已由
`_intent_checks._checks_from_expected` 发 `field_constraint_check`（读 DOM 属性判定，
落 `payload_json["checks"]`）。但生成端**残留**同一期望的 verify 步骤于
`test_method.verifications`，Step4 guided 会对它做文本/语义判定 → 用错工具 → 恒 FAIL
（实测 TC-REQ-006-I01）。

生成端已在 `_navigation_resolver` 加 RC-S 修复（新用例不再产生残留）。本脚本按
**同一单一事实源**（is_field_constraint_requirement + parse_field_constraints）清理
存量用例，属「存量数据库须在代码修复后人工更新脏数据」的既定约定。

用法:
  python scripts/maintenance/migrate_rc_s_verify_steps.py --project accept_sut_final [--dry-run]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dotenv import load_dotenv

load_dotenv()
import pymysql

from src.pipeline.step3._rule_extractor_keywords import (
    is_field_constraint_requirement,
    parse_field_constraints,
)


def db():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT")),
        user=os.getenv("MYSQL_USER"), password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE"), charset="utf8mb4",
    )


def _is_constraint_verify(v: dict) -> bool:
    exp = v.get("expect")
    if not isinstance(exp, str) or not exp.strip():
        return False
    return bool(is_field_constraint_requirement(exp) and parse_field_constraints(exp))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    c = db()
    cur = c.cursor()
    cur.execute(
        "SELECT case_id, payload_json FROM test_cases WHERE project=%s", (a.project,)
    )
    rows = cur.fetchall()
    changed, total_dropped = [], 0
    for cid, pj in rows:
        if not pj:
            continue
        try:
            obj = json.loads(pj)
        except Exception:
            continue
        tm = ((obj.get("_explore_task") or {}).get("test_method") or {})
        vrs = tm.get("verifications")
        if not isinstance(vrs, list) or not vrs:
            continue
        kept = [v for v in vrs if not (isinstance(v, dict) and _is_constraint_verify(v))]
        if len(kept) == len(vrs):
            continue
        dropped = [v for v in vrs if isinstance(v, dict) and _is_constraint_verify(v)]
        total_dropped += len(dropped)
        changed.append((cid, [str(d.get("expect"))[:50] for d in dropped]))
        if a.dry_run:
            continue
        tm["verifications"] = kept
        cur.execute(
            "UPDATE test_cases SET payload_json=%s WHERE project=%s AND case_id=%s",
            (json.dumps(obj, ensure_ascii=False), a.project, cid),
        )

    if not a.dry_run:
        c.commit()
    c.close()

    print("用例总数=%d ; 受影响用例=%d ; 删除验证点=%d %s"
          % (len(rows), len(changed), total_dropped, "(DRY-RUN)" if a.dry_run else ""))
    for cid, exps in changed:
        print("  %-20s 删除: %s" % (cid, "; ".join(exps)))
    return 0


if __name__ == "__main__":
    sys.exit(main())