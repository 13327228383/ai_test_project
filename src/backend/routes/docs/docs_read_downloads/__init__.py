# -*- coding: utf-8 -*-
"""
文档中心 — 文档下载路由 (package facade)
========================================
需求稿、功能清单、Figma 统计、测试用例文件、需求拆解表、执行人列表。
"""

# 导入子模块以注册路由（@docs_bp.route 装饰器在 import 时生效）
from src.backend.routes.docs.docs_read_downloads import _downloads  # noqa: F401
from src.backend.routes.docs.docs_read_downloads import _queries  # noqa: F401

__all__ = []
