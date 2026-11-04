# -*- coding: utf-8 -*-
# DatabaseAgent — 场景驱动的通用数据库操作代理（内置场景模板 + 外键感知插入策略）
#
# Facade 文件：将原始 DatabaseAgent 拆分为多个 Mixin，通过多继承组合。
# 外部导入路径保持不变：from src.extensions.db.db_agent import db_agent / DatabaseAgent

import threading
import logging

from src.extensions.db.db_adapters import DbAdapterMixin  # 数据库适配器 Mixin

from src.extensions.db.db_agent.connection import DatabaseAgentConnectionMixin
from src.extensions.db.db_agent.insert import DatabaseAgentInsertMixin
from src.extensions.db.db_agent.schema import DatabaseAgentSchemaMixin
from src.extensions.db.db_agent.cleanup import DatabaseAgentCleanupMixin
from src.extensions.db.db_agent.test_data import DatabaseAgentTestDataMixin

_log = logging.getLogger(__name__)


class DatabaseAgent(
    DbAdapterMixin,
    DatabaseAgentConnectionMixin,
    DatabaseAgentInsertMixin,
    DatabaseAgentSchemaMixin,
    DatabaseAgentCleanupMixin,
    DatabaseAgentTestDataMixin,
):
    """
    通用数据库操作代理。

    核心方法：prepare_test_data / cleanup_test_data / execute / query / insert
    测试数据标记策略：所有自动生成的数据使用 'TEST_' 前缀标记，cleanup_test_data() 通过此前缀批量清理。
    """

    pass


class _ThreadSafeDbAgent:
    """线程安全的 DatabaseAgent 代理，每线程持有独立实例，避免并发串台。"""

    def __init__(self):
        self._local = threading.local()

    def _get_instance(self):
        if not hasattr(self._local, "agent"):
            self._local.agent = DatabaseAgent()
        return self._local.agent

    def __getattr__(self, name):
        return getattr(self._get_instance(), name)

    def __setattr__(self, name, value):
        if name == "_local":
            object.__setattr__(self, name, value)
        else:
            setattr(self._get_instance(), name, value)


# 模块级线程安全代理（外部 import db_agent 无需改动）
db_agent = _ThreadSafeDbAgent()
