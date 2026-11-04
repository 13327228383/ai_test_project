# -*- coding: utf-8 -*-
"""按 case_type 剖析失败用例，判断跨切面检查是否被注入到非本类型用例。"""
import os
from collections import Counter
import pymysql
from dotenv import load_dotenv

load_dotenv()
c = pymysql.connect(
    host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT")),
    user=os.getenv("MYSQL_USER"), password=os.getenv("MYSQL_PASSWORD"),
    database=os.getenv("MYSQL_DATABASE"), charset="utf8mb4",
)
cur = c.cursor()
P = "accept_sut_0923"

print("== case_type x status ==")
cur.execute(
    "SELECT tc.case_type, e.status, COUNT(*) FROM executions e "
    "JOIN test_cases tc ON tc.case_id=e.case_id AND tc.project=e.project "
    "WHERE e.project=%s GROUP BY tc.case_type, e.status ORDER BY 1,2", (P,))
for r in cur.fetchall():
    print(r)

print("\n== FAIL 用例的 case_type 分布 ==")
cur.execute(
    "SELECT tc.case_type, COUNT(*) FROM executions e "
    "JOIN test_cases tc ON tc.case_id=e.case_id AND tc.project=e.project "
    "WHERE e.project=%s AND e.status='FAIL' GROUP BY tc.case_type ORDER BY 2 DESC", (P,))
for r in cur.fetchall():
    print(r)

print("\n== 含 viewport/click_target 的 FAIL 用例 case_type 分布 ==")
cur.execute(
    "SELECT tc.case_type, COUNT(*) FROM executions e "
    "JOIN test_cases tc ON tc.case_id=e.case_id AND tc.project=e.project "
    "WHERE e.project=%s AND e.status='FAIL' AND (e.failure_reason LIKE '%%viewport_rendering%%' "
    "OR e.failure_reason LIKE '%%click_target_size%%') GROUP BY tc.case_type ORDER BY 2 DESC", (P,))
for r in cur.fetchall():
    print(r)

print("\n== 样例：viewport FAIL 用例的 checks ==")
cur.execute(
    "SELECT tc.case_id, tc.case_type, tc.title, tc.case_checks_json FROM test_cases tc "
    "JOIN executions e ON e.case_id=tc.case_id AND e.project=tc.project "
    "WHERE e.project=%s AND e.failure_reason LIKE '%%viewport_rendering%%' LIMIT 2", (P,))
for r in cur.fetchall():
    print(r[0], "|", r[1], "|", str(r[2])[:60])
    print("   checks:", str(r[3])[:600])
c.close()