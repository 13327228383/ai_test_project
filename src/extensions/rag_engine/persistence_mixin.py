# -*- coding: utf-8 -*-
"""
MySQL 持久化、ChromaDB 交互、索引加载/清空

Facade：将 PersistenceSaveMixin / PersistenceLoadMixin / PersistenceExportMixin
组合为统一的 PersistenceMixin，保持外部导入路径不变。
"""

from src.extensions.rag_engine.persistence_mixin_mixins._save import (
    PersistenceSaveMixin,
)
from src.extensions.rag_engine.persistence_mixin_mixins._load import (
    PersistenceLoadMixin,
)
from src.extensions.rag_engine.persistence_mixin_mixins._export import (
    PersistenceExportMixin,
)


class PersistenceMixin(
    PersistenceSaveMixin, PersistenceLoadMixin, PersistenceExportMixin
):
    """MySQL 持久化、索引加载/清空相关方法。"""

    pass
