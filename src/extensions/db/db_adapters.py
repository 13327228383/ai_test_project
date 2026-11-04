# -*- coding: utf-8 -*-
"""
数据库适配器 Mixin
==================
包含所有 DB 类型特定的连接、查询、语法差异方法。
DatabaseAgent 通过多重继承整合此类。

支持的数据库: YasDB, MySQL, PostgreSQL, Oracle, SQLite

本模块为 facade，实际实现拆分到 db_adapters_mixins/ 子包中：
- _connect.py   : 连接相关
- _execute.py   : SQL 执行与查询
- _schema.py    : Schema 操作与 FLASHBACK
"""

from .db_adapters_mixins._connect import DbAdapterConnectMixin
from .db_adapters_mixins._execute import DbAdapterExecuteMixin
from .db_adapters_mixins._schema import DbAdapterSchemaMixin


class DbAdapterMixin(
    DbAdapterConnectMixin, DbAdapterExecuteMixin, DbAdapterSchemaMixin
):
    """DB 适配器 Mixin — 提供跨数据库类型的连接和 SQL 执行能力。"""

    pass
