# -*- coding: utf-8 -*-
"""诊断：run_summary（Dashboard 通过/失败 的权威来源）与 executions 实时口径对照。"""
import os

import pymysql
from dotenv import load_dotenv

load_dotenv()
P = "accept_sut_0923"
c = pymysql.connect(
    host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT")),
    user=os.getenv("MYSQL_USER"), password=os.getenv("MYSQL_PASSWORD"),
    database=os.getenv("MYSQL_DATABASE"), charset="utf8mb4",
)
cur = c.cursor()
cur.execute("SHOW COLUMNS FROM run_summary")
print("run_summary cols:", [r[0] for r in cur.fetchall()])

cur.execute(
    "SELECT id, project, version_id, run_id, total_cases, total_passed, total_failed, "
    "pass_rate, created_at FROM run_summary WHERE project=%s ORDER BY created_at DESC LIMIT 10",
    (P,),
)
print("\n== run_summary (recent) ==")
for r in cur.fetchall():
    print(r)

cur.execute("SELECT status, COUNT(1) FROM executions WHERE project=%s GROUP BY status", (P,))
print("\n== executions live ==")
for r in cur.fetchall():
    print(r)

cur.execute("SELECT case_type, COUNT(1) FROM test_cases WHERE project=%s GROUP BY case_type", (P,))
print("\n== test_cases by type ==")
for r in cur.fetchall():
    print(r)

cur.execute("SELECT MAX(executed_at) FROM executions WHERE project=%s", (P,))
print("\nmax executed_at:", cur.fetchone())

cur.execute(
    "SELECT COUNT(1) FROM test_cases WHERE project=%s AND case_id LIKE 'TC-PAGE-%%'", (P,))
print("TC-PAGE-* cases:", cur.fetchone())
c.close()