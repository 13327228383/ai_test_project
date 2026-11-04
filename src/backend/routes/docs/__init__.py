# -*- coding: utf-8 -*-
"""
文档中心 路由包
==============
docs_read:  GET 读取路由
docs_write: POST 写入路由
docs_smoke: 核心验证测试用例 + 后台生成
"""

from flask import Blueprint

docs_bp = Blueprint("docs", __name__)

# ★ 导入子模块以注册路由（必须在 Blueprint 定义之后）
from src.backend.routes.docs import docs_read  # noqa: E402, F811
from src.backend.routes.docs import docs_write  # noqa: E402, F811
from src.backend.routes.docs import docs_smoke  # noqa: E402, F811

# ★ 新增(07-05h): 关系链路统一API
from src.backend.routes.docs import docs_trace_routes  # noqa: E402, F811

docs_trace_routes._register_trace_routes(docs_bp)
