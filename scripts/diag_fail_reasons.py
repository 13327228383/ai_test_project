# -*- coding: utf-8 -*-
"""剖析 accept_sut_0923 失败原因分布（定位引擎缺陷 vs 靶子系统问题）。"""
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

cur.execute(
    "SELECT status, failure_reason FROM executions WHERE project=%s", (P,))
rows = cur.fetchall()
print("total=%d" % len(rows))

for st in ("ERROR", "FAIL"):
    print("\n===== %s =====" % st)
    cnt = Counter()
    samples = {}
    for status, fr in rows:
        if status != st:
            continue
        key = (fr or "<NULL>").strip().replace("\n", " ")[:160]
        cnt[key] += 1
        samples.setdefault(key, (status, fr))
    for k, n in cnt.most_common(15):
        print("[%4d] %s" % (n, k))
c.close()