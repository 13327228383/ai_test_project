# -*- coding: utf-8 -*-
"""RC-AE 存量数据迁移：把 payload_json.checks 里的**旧反馈条词表**替换为单一事实源。

用法:
  python scripts/maintenance/migrate_rc_ae_selectors.py [project] [--dry-run]

背景（根因 §RC-AE）:
  Step3 生成端此前各自维护硬编码反馈条词表（4 份互不相同），生成的
  element_exists / field_error_visible_check 选择器**不含** `.flash-danger`
  / `[data-testid*='error']` 等框架无关形态 → 靶子正确渲染错误条时恒 0 命中
  → 系统性假 FAIL（accept_sut_final 实测 64 条 I02/I03/N01/B01）。
  代码已改为统一从 src/pipeline/utils/feedback_selectors.py 取词表；
  已入库用例的选择器是**快照**，需本脚本一次性回填。

幂等判据: 新词表必含 `.flash-danger`（错误）/ `.flash-success`（成功）/
  `[aria-invalid='true']`（校验），旧词表必不含 → 已迁移的行不会被二次改写。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dotenv import load_dotenv

load_dotenv()
import pymysql

from src.pipeline.utils.feedback_selectors import (  # noqa: E402
    ERROR_SELECTOR_CSV, VALIDATION_SELECTOR_CSV, SUCCESS_SELECTOR_CSV,
)

LOGDIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs"
)

# 旧词表的判别标记（任一命中即视为旧词表；新词表已含 flash-* / aria-invalid 故不命中）
_ERR_MARKERS = (
    ".ant-form-item-explain-error",
    ".el-form-item__error",
    ".field-error",
    ".error-message",
)
_SUC_MARKERS = (
    ".ant-message-success",
    ".el-message--success",
    ".alert-success",
)


def classify_legacy(sel: str) -> str:
    """返回 'error' / 'validation' / 'success' / ''（非旧词表，不动）。"""
    s = str(sel or "").strip()
    if not s:
        return ""
    if "[aria-invalid" in s and "flash-" not in s:
        return "validation"
    if any(m in s for m in _ERR_MARKERS) and ".flash-danger" not in s:
        return "error"
    if any(m in s for m in _SUC_MARKERS) and ".flash-success" not in s:
        return "success"
    return ""


_REPLACEMENT = {
    "error": ERROR_SELECTOR_CSV,
    "validation": VALIDATION_SELECTOR_CSV,
    "success": SUCCESS_SELECTOR_CSV,
}


def walk_replace(node, stats, where):
    """递归替换 checks 结构里的 selector；返回是否有改动。"""
    changed = False
    if isinstance(node, dict):
        sel = node.get("selector")
        if isinstance(sel, str):
            kind = classify_legacy(sel)
            if kind:
                node["selector"] = _REPLACEMENT[kind]
                stats[kind] = stats.get(kind, 0) + 1
                changed = True
        for k, v in list(node.items()):
            if walk_replace(v, stats, where):
                changed = True
    elif isinstance(node, list):
        for v in node:
            if walk_replace(v, stats, where):
                changed = True
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", nargs="?", default="accept_sut_final")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    c = pymysql.connect(
        host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT")),
        user=os.getenv("MYSQL_USER"), password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE"), charset="utf8mb4",
    )
    cur = c.cursor()
    cur.execute(
        "SELECT case_id, payload_json FROM test_cases WHERE project=%s ORDER BY case_id",
        (a.project,),
    )
    rows = cur.fetchall()
    print("扫描用例 %d 条 (project=%s, dry_run=%s)" % (len(rows), a.project, a.dry_run))

    stats = {}
    touched = []
    backup = {}
    updates = []
    for cid, pj in rows:
        if not pj:
            continue
        try:
            p = json.loads(pj)
        except Exception:
            continue
        before = json.dumps(p, ensure_ascii=False, sort_keys=True)
        if not walk_replace(p, stats, cid):
            continue
        after = json.dumps(p, ensure_ascii=False, sort_keys=True)
        if before == after:
            continue
        touched.append(cid)
        backup[cid] = pj
        updates.append((json.dumps(p, ensure_ascii=False), cid))

    print("命中用例 %d 条；替换次数分布: %s" % (len(touched), stats))
    if touched:
        print("样例: %s" % ", ".join(touched[:8]))

    if a.dry_run:
        print("DRY-RUN：未写库。VERDICT migrate: PASS(dry)")
        c.close()
        return 0

    if not updates:
        print("无需要迁移的行（幂等）。VERDICT migrate: PASS(no-op)")
        c.close()
        return 0

    os.makedirs(LOGDIR, exist_ok=True)
    bp = os.path.join(LOGDIR, "_rc_ae_backup_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    with open(bp, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False)
    print("备份 %d 行 → %s" % (len(backup), bp))

    n = 0
    for payload_json, cid in updates:
        cur.execute(
            "UPDATE test_cases SET payload_json=%s WHERE project=%s AND case_id=%s",
            (payload_json, a.project, cid),
        )
        n += cur.rowcount
    c.commit()
    print("已更新 %d 行" % n)
    c.close()
    print("VERDICT migrate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())