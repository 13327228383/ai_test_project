# -*- coding: utf-8 -*-
"""
会话上下文管理器 (SessionContext)
==================================
核心职责：
1. 跟踪浏览器会话状态（是否已登录、当前用户/角色、当前页面）
2. 按流水线阶段分级提供系统知识：
   - Step2（需求拆解）：DB表列表 + 实体关系概要
   - Step3（测试用例生成）：表字段类型/约束
   - Step4（执行阶段）：不注入，仅提供登录状态供 StepOptimizer 做确定性预处理

设计原则：
- 登录状态检测基于 DOM 特征，不依赖固定选择器
- DB 元数据从连接动态查询，无需额外配置文件
- 缓存机制避免重复检测（可配置有效期）

实现方式：Facade + Mixins 模式
- SessionStateMixin: 会话状态管理
- SessionDbMetaMixin: DB 元数据查询
- SessionKnowledgeMixin: 流水线知识注入
"""

import logging

from src.core.config_loader import config
from .state import SessionStateMixin
from .db_meta import SessionDbMetaMixin
from .knowledge import SessionKnowledgeMixin

_log = logging.getLogger("session_context")


class SessionContext(SessionStateMixin, SessionDbMetaMixin, SessionKnowledgeMixin):
    """会话上下文管理器。

    通过多继承组合三个 Mixin 的能力：
    - SessionStateMixin: 浏览器会话状态跟踪与登录检测
    - SessionDbMetaMixin: 数据库元数据查询与关系发现
    - SessionKnowledgeMixin: 按流水线阶段注入系统知识
    """

    pass


# ★ 方案C 改造：session_ctx 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

session_ctx = PerProjectRegistry(SessionContext)
