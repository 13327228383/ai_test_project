# -*- coding: utf-8 -*-
"""查看历史 Step3/Step4 耗时，估算重跑成本。"""
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
cur.execute("SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema=%s "
            "AND table_name='run_logs' ORDER BY ordinal_position",
            (os.getenv("MYSQL_DATABASE"),))
print("run_logs cols:", [r[0] for r in cur.fetchall()])

cur.execute("SELECT id, project, step_num, message, created_at FROM run_logs "
            "WHERE project='accept_sut_0923' ORDER BY id DESC LIMIT 40")
for r in cur.fetchall():
    print(r[0], r[2], str(r[3])[:90], r[4])

print("\n== run 起止（RUN_START/RUN_END）==")
cur.execute("SELECT project, step_num, message, created_at FROM run_logs "
            "WHERE project='accept_sut_0923' AND (message LIKE '%%RUN_START%%' "
            "OR message LIKE '%%RUN_END%%') ORDER BY id DESC LIMIT 20")
for r in cur.fetchall():
    print(r[1], str(r[2])[:70], r[3])
c.close()