# -*- coding: utf-8 -*-
"""
文档中心 — 读取路由（Facade）
============================
原 docs_read.py 已按功能域拆分为子模块，本文件保留为 facade，
通过导入子模块触发路由注册，保持对既有消费者的兼容性。

拆分模块：
- docs_read_downloads : 报告目录 / 需求稿 / 导航地图 / Figma 统计 / 测试用例文件 / 需求拆解表 / 执行人列表
- docs_read_coverage  : 覆盖率
- docs_read_execution : 执行结果 ZIP 导出
- docs_read_stats     : 多维度统计报告 + 多格式导出（md/xlsx/docx/pdf）
- docs_read_traceability : 追溯链路 / 变更对比 / RAG 面板
- docs_read_versions  : 文档版本管理
"""

# 导入子模块以注册路由（必须在 Blueprint 定义之后，由 __init__.py 保证顺序）
from src.backend.routes.docs import (
    docs_read_downloads,  # noqa: F401
    docs_read_coverage,  # noqa: F401
    docs_read_execution,  # noqa: F401
    docs_read_stats,  # noqa: F401
    docs_read_traceability,  # noqa: F401
    docs_read_versions,  # noqa: F401
)
