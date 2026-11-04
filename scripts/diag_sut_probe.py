# -*- coding: utf-8 -*-
"""探活 fullcov_sut：真实登录 + 列表页 + 关键 data-testid 存在性。"""
import re
import sys

import requests

BASE = "http://127.0.0.1:8050"
s = requests.Session()

r = s.get(BASE + "/login", timeout=10)
print(f"GET /login -> {r.status_code} len={len(r.text)}")

r = s.post(
    BASE + "/login",
    data={"username": "admin", "password": "admin123"},
    timeout=10,
    allow_redirects=True,
)
print(f"POST /login -> {r.status_code} url={r.url} len={len(r.text)}")

r = s.get(BASE + "/assets", timeout=10)
print(f"GET /assets -> {r.status_code} len={len(r.text)}")

html = r.text
for tid in (
    "table-assets", "btn-new-asset", "pagination-current",
    "input-title", "input-price", "input-stock", "btn-save",
):
    print(f"  data-testid={tid:<22} {'FOUND' if tid in html else 'MISSING'}")

rows = re.findall(r"<tr", html)
print(f"  <tr> count = {len(rows)}")
m = re.search(r"共\s*(\d+)\s*条", html)
print(f"  '共 N 条' = {m.group(1) if m else 'N/A'}")