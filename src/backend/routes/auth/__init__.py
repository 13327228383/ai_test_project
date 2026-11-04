# -*- coding: utf-8 -*-
"""
认证与用户管理路由 Blueprint — 子模块聚合入口
原 auth.py 拆分后的 re-export，保持外部导入不变。
"""

from src.backend.routes.auth._core import auth_bp

# 导入各子模块以注册路由
from src.backend.routes.auth import _login
from src.backend.routes.auth import _key
from src.backend.routes.auth import _user
from src.backend.routes.auth import _credential
from src.backend.routes.auth import _permission
from src.backend.routes.auth import _role

__all__ = ["auth_bp"]
