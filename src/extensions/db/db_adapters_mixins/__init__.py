# -*- coding: utf-8 -*-
"""
db_adapters_mixins
==================
DbAdapterMixin 的拆分实现（facade + mixins 模式）。
"""

from ._connect import DbAdapterConnectMixin
from ._execute import DbAdapterExecuteMixin
from ._schema import DbAdapterSchemaMixin

__all__ = [
    "DbAdapterConnectMixin",
    "DbAdapterExecuteMixin",
    "DbAdapterSchemaMixin",
]
