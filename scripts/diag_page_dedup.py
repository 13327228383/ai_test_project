# -*- coding: utf-8 -*-
"""检查 兼容/易用 用例的页面归属多样性，确定去重键。"""
import json
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
cur.execute("SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema=%s "
            "AND table_name='test_cases' ORDER BY ordinal_position",
            (os.getenv("MYSQL_DATABASE"),))
cols = [r[0] for r in cur.fetchall()]
print("test_cases cols:", cols)

cur.execute("SELECT * FROM test_cases WHERE project=%s AND case_type IN ('兼容','易用')", (P,))
rows = [dict(zip(cols, r)) for r in cur.fetchall()]
print("兼容/易用 case 数 =", len(rows))

navs = Counter()
for d in rows:
    nav = ""
    try:
        acts = json.loads(d.get("case_actions_json") or "[]")
        for a in acts:
            if isinstance(a, dict) and a.get("type") == "navigate":
                nav = str(a.get("target_hint") or "")
                break
    except Exception:
        pass
    navs[nav] += 1
print("\n兼容/易用 用例的 navigate target_hint 分布（去重键候选）:")
for k, n in navs.most_common(30):
    print("  [%3d] %r" % (n, k))
print("distinct nav =", len(navs))

# 需求 module 分布
cur.execute("SELECT module, COUNT(*) FROM requirements WHERE project=%s GROUP BY module "
            "ORDER BY 2 DESC LIMIT 30", (P,))
print("\n需求 module 分布:")
for r in cur.fetchall():
    print("  ", r)
c.close()