# -*- coding: utf-8 -*-
"""历史 Step4 全量 run 的起止与耗时（判断重跑可行性）。"""
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
cur.execute(
    "SELECT created_at, message FROM run_logs WHERE project='accept_sut_0923' "
    "AND (message LIKE '%%RUN_START%%' OR message LIKE '%%RUN_END%%' "
    "OR message LIKE '%%执行完成，耗时%%') ORDER BY id ASC")
for r in cur.fetchall():
    print(r[0], "|", str(r[1])[:110])
c.close()