# -*- coding: utf-8 -*-
"""列出 accept_sut_0923 需求，检查是否声明了 兼容/响应式/移动端/易用 类诉求。"""
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
P = "accept_sut_0923"
cur.execute(
    "SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema=%s "
    "AND table_name='requirements' ORDER BY ordinal_position", (os.getenv("MYSQL_DATABASE"),))
cols = [r[0] for r in cur.fetchall()]
print("requirements cols:", cols)

cur.execute("SELECT * FROM requirements WHERE project=%s", (P,))
rows = cur.fetchall()
print("req count =", len(rows))
KW = ("兼容", "响应式", "移动", "手机", "viewport", "分辨率", "易用", "可访问", "无障碍",
      "wcag", "点击区域", "触控", "移动端")
for r in rows:
    d = dict(zip(cols, r))
    txt = " ".join(str(d.get(k) or "") for k in cols if k not in ("id", "project"))
    hits = [k for k in KW if k.lower() in txt.lower()]
    print("-", d.get("req_id"), "|", str(d.get("title") or "")[:50], "| hits:", hits)
c.close()