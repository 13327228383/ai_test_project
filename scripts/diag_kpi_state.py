# -*- coding: utf-8 -*-
"""诊断 Dashboard KPI（通过/失败、已执行）取数口径与当前 DB 真实状态。"""
import os
import pymysql
from dotenv import load_dotenv

load_dotenv()
c = pymysql.connect(
    host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT")),
    user=os.getenv("MYSQL_USER"), password=os.getenv("MYSQL_PASSWORD"),
    database=os.getenv("MYSQL_DATABASE"), charset="utf8mb4",
)
cur = c.cursor()


def show(title, sql, args=()):
    print("\n== %s ==" % title)
    cur.execute(sql, args)
    print(" | ".join(d[0] for d in cur.description))
    for r in cur.fetchall():
        print(r)


for p in ("accept_sut_0923", "accept_sut_final"):
    show("executions by status %s" % p,
         "SELECT version_id,status,COUNT(*) FROM executions WHERE project=%s "
         "GROUP BY version_id,status ORDER BY version_id,status", (p,))
    show("recent exec today %s" % p,
         "SELECT status,COUNT(*),MIN(executed_at),MAX(executed_at) FROM executions "
         "WHERE project=%s AND executed_at>='2026-09-26 17:00:00' GROUP BY status", (p,))
    show("test_cases %s" % p,
         "SELECT COUNT(*) FROM test_cases WHERE project=%s", (p,))

show("run_summary accept_sut_0923",
     "SELECT * FROM run_summary WHERE project=%s ORDER BY id DESC LIMIT 3",
     ("accept_sut_0923",))
show("project_versions",
     "SELECT id,project,version_name,version_label,created_at FROM project_versions "
     "WHERE project IN ('accept_sut_0923','accept_sut_final') ORDER BY id DESC LIMIT 10")
show("executions columns",
     "SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema=%s "
     "AND table_name='executions' ORDER BY ordinal_position",
     (os.getenv("MYSQL_DATABASE"),))
c.close()