# -*- coding: utf-8 -*-
"""诊断并修复 DB 轨凭据配置（环境配置问题，非引擎代码缺陷）。

背景：DB 轨断言全部报 "Access denied for user 'fubo'"。
追查链：db_verifier → db_agent.connect() → config_loader.config.db_user
       → _user_cred("DB_USER") → user_credentials_v2 表（FUBO 靶子时代遗留凭据）
引擎行为本身正确（连接失败 → skipped，不计入 AND，置信度扣分），
故归为**环境配置问题**，不修改引擎判定逻辑，只修正配置数据。

用法：
  python scripts/case_runner/fix_db_cred.py            # 诊断
  python scripts/case_runner/fix_db_cred.py --apply    # 修复（写入真实凭据）
"""
from __future__ import annotations

import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _env():
    d = {}
    for line in open(os.path.join(_ROOT, ".env"), encoding="utf-8", errors="ignore"):
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if m:
            d[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return d


def _conn():
    import pymysql

    e = _env()
    pw = e.get("STORAGE_MYSQL_PASSWORD") or e.get("MYSQL_PASSWORD") or ""
    return pymysql.connect(
        host="127.0.0.1", port=3306, user="root", password=pw,
        database="ai_test_engine", charset="utf8mb4",
    )


def diagnose():
    c = _conn()
    cur = c.cursor()
    cur.execute(
        "SELECT user_id, project, `key`, LEFT(`value`,30) FROM user_credentials_v2 "
        "WHERE `key` LIKE %s OR `key` LIKE %s",
        ("DB_%", "TARGET_DB%"),
    )
    rows = cur.fetchall()
    print("=== user_credentials_v2 中 DB 相关凭据 ===")
    for r in rows or []:
        print("  user=%s project=%s key=%s value=%s..." % r)
    if not rows:
        print("  (无)")
    c.close()


def apply_fix():
    """写入真实可用的 DB 凭据（供 DB 轨断言真实执行）。"""
    e = _env()
    real_user = e.get("STORAGE_MYSQL_USER") or "root"
    real_pw = e.get("STORAGE_MYSQL_PASSWORD") or ""

    from src.users.credentials import set_user_credential

    set_user_credential("admin", "DB_USER", real_user, project="*")
    set_user_credential("admin", "DB_PASSWORD", real_pw, project="*")
    print("[apply] 已写入 DB_USER / DB_PASSWORD for admin")

    # 验证读回
    from src.core.config_loader import config

    print("[verify] config.db_user =", config.db_user)
    print("[verify] config.db_host =", config.db_host, "port =", config.db_port)


if __name__ == "__main__":
    if "--apply" in sys.argv:
        apply_fix()
    else:
        diagnose()
