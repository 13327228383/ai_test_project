# -*- coding: utf-8 -*-
"""
文档中心 — 写入路由
====================
需求稿上传、AI 补全、XMind 设计、RAG 索引重建。
从 docs_core.py 拆分出来。
"""

# ★ 导入子模块以注册路由到 docs_bp
from src.backend.routes.docs.docs_write import _requirement  # noqa: E402, F811
from src.backend.routes.docs.docs_write import _xmind  # noqa: E402, F811
from src.backend.routes.docs.docs_write import _rag  # noqa: E402, F811
