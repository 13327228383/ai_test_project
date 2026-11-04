# -*- coding: utf-8 -*-
"""章节标题归一化 + 已知事实勘误。

统一各章首行标题为「# 第 N 章　标题」，并修正撰写过程中确认的数据错误。
可重复执行；无匹配时静默跳过。
"""
from __future__ import annotations

import os
import re

BOOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TITLES = {
    "00": "前言：这本书是怎么写成的",
    "01": "系统定位与能力边界",
    "02": "技术选型",
    "03": "分层架构与模块地图",
    "04": "端到端数据流",
    "05": "Step1 · 需求数据拉取",
    "06": "Step2 · 需求拆解与防幻觉",
    "07": "Step3 · 用例建模",
    "08": "Step4 · 混合执行引擎总览",
    "09": "Step4 · 浏览器执行核心",
    "10": "Step4 · 三轨校验与断言",
    "11": "Step4 · 接口与数据执行轨",
    "12": "Step5 · 报告与补测建议",
    "13": "度量与健康",
    "14": "横切质量门禁",
    "15": "智能体与动作库",
    "16": "检索与知识资产",
    "17": "置信度与预言机",
    "18": "Web 控制台 · 装配与中间件",
    "19": "Web 控制台 · 路由族",
    "20": "Web 控制台 · 前端与实时日志",
    "21": "数据模型与数据库",
    "22": "配置体系",
    "23": "用户与权限",
    "24": "测试体系",
    "25": "质量门禁防线",
    "26": "部署与运行",
    "27": "诊断与运维工具",
    "28": "关键设计一 · 防幻觉体系",
    "29": "关键设计二 · 单管道三策略",
    "30": "关键设计三 · 三轨校验与精度模型",
    "31": "关键设计四 · 语义唯一源",
    "32": "关键设计五 · 误判根因库",
    "33": "关键设计六 · 三十万行规模治理",
    "A": "附录 A · 文件职责索引",
    "B": "附录 B · 数据表速查",
    "C": "附录 C · HTTP 接口清单",
    "D": "附录 D · 术语与命名对照",
}

# 已确认的事实勘误：(章节前缀, 原文, 修正)
ERRATA = [
    ("28", "`clarification_service.py`（4.3 万行）", "`clarification_service.py`（983 行）"),
]


def main() -> None:
    changed = 0
    for fn in sorted(os.listdir(BOOK)):
        if not fn.endswith(".md"):
            continue
        key = fn.split("-")[0]
        if key not in TITLES:
            continue
        path = os.path.join(BOOK, fn)
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        lines = text.split("\n")
        want = f"# 第 {int(key)} 章　{TITLES[key]}" if key != "A" and key.isdigit() else f"# {TITLES[key]}"
        if lines and lines[0].startswith("# ") and lines[0] != want:
            lines[0] = want
            text = "\n".join(lines)
            changed += 1
        for prefix, old, new in ERRATA:
            if key == prefix and old in text:
                text = text.replace(old, new)
                print(f"  勘误 {fn}: {old} -> {new}")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(f"标题归一化完成，改动 {changed} 个文件")


if __name__ == "__main__":
    main()
