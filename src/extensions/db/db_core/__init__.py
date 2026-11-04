# -*- coding: utf-8 -*-
"""
通用数据库操作工具 (DbExecutor)
===============================
核心设计：彻底解耦业务逻辑，通过 LLM 将自然语言意图自动转换为 SQL，
避免为每个测试用例写硬编码的数据准备脚本。

实现方式：DbExecutor 通过 mixin 多继承组装各职责模块：
  - _DbCoreBaseMixin       — 配置初始化、重载、表结构文档读取
  - _DbCoreGenerateMixin   — LLM SQL 生成
  - _DbCoreValidateMixin   — SQL 安全校验
  - _DbCoreExecuteMixin    — SQL 执行（调度 + 5 种数据库实现）
  - _DbCoreOrchestrateMixin — 核心编排与便捷方法

使用方式：
    from src.extensions.db.db_core import db_executor

    # 读取表结构文档
    with open("db_schema.md", encoding="utf-8") as f:
        schema = f.read()

    # 一句话搞定数据准备
    result = db_executor.execute_natural_language_sql(
        intent="清空所有任务数据",
        schema_context=schema,
    )

    # 或者批量插入
    result = db_executor.execute_natural_language_sql(
        intent="在 users 表中插入 100 条测试用户数据",
        schema_context=schema,
    )
"""

from src.extensions.db.db_core.base import _DbCoreBaseMixin
from src.extensions.db.db_core.generate import _DbCoreGenerateMixin
from src.extensions.db.db_core.validate import _DbCoreValidateMixin
from src.extensions.db.db_core.execute import _DbCoreExecuteMixin
from src.extensions.db.db_core.orchestrate import _DbCoreOrchestrateMixin


class DbExecutor(
    _DbCoreBaseMixin,
    _DbCoreGenerateMixin,
    _DbCoreValidateMixin,
    _DbCoreExecuteMixin,
    _DbCoreOrchestrateMixin,
):
    """
    通用数据库操作执行器。

    核心方法：
        execute_natural_language_sql(intent, schema_context, auto_mode=False)
        输入自然语言意图 + 表结构文档 → LLM 生成 SQL → 安全确认 → 执行。
    """

    pass


# =============================================================================
# 模块级单例实例
# =============================================================================
# ★ 方案C 改造：db_executor 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

db_executor = PerProjectRegistry(DbExecutor)
