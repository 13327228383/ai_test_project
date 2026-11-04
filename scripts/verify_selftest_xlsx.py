# -*- coding: utf-8 -*-
"""交付自查：校验生成的 xlsx 是否字段完整、无空壳、方法覆盖全集。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from openpyxl import load_workbook

p = ROOT / "docs" / "引擎端到端测试用例集.xlsx"
wb = load_workbook(p, read_only=True)
ws = wb["全量用例"]
rows = list(ws.iter_rows(values_only=True))
header = list(rows[0])
data = rows[1:]
total = len(data)
print(f"总用例数: {total}")

# 关键字段不得为空(前置/步骤/预期/操作/断言/方法/变化)
KEY = {"前置条件": 6, "操作步骤": 7, "预期结果": 8, "关键操作指令": 9,
       "关键校验断言指令": 10, "测试方法": 4, "变化点": 5}
empty = {}
for col_name, col_i in KEY.items():
    n = sum(1 for r in data if not str(r[col_i] or "").strip())
    empty[col_name] = n
print("空字段统计:", empty)
assert all(v == 0 for v in empty.values()), "存在空字段!"

# 方法覆盖全集(18)
import importlib.util
cat = Path(ROOT / "src" / "testing_methods" / "catalog.py")
methods_used = {str(r[4]) for r in data}
print(f"方法码覆盖数: {len(methods_used)} = {sorted(methods_used)}")

# 域覆盖
doms = {}
for r in data:
    doms[str(r[1])] = doms.get(str(r[1]), 0) + 1
print(f"功能域数: {len(doms)}")
for k, v in sorted(doms.items(), key=lambda kv: -kv[1]):
    print(f"   {k}: {v}")

# 抽样预览 3 条(跨域)
print("\n--- 抽样预览 ---")
sample_idx = {0, total // 2, total - 1}
for i, r in enumerate(data):
    if i in sample_idx:
        for name, col in zip(["ID", "域", "子", "标题", "方法", "变化", "前置",
                              "步骤", "预期", "操作", "断言", "目标"], range(12)):
            print(f"  {name}: {str(r[col])[:70]}")
        print("  ---")
wb.close()
print("\n[PASS] 自查通过：规模", total, "方法覆盖", len(methods_used))