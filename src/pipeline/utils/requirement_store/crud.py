# -*- coding: utf-8 -*-
"""需求 CRUD facade — 将读写操作拆分为 _write / _read mixin，本文件仅做组合。零逻辑变更。"""

from src.pipeline.utils.requirement_store.crud_mixins._read import (
    RequirementStoreReadMixin,
)
from src.pipeline.utils.requirement_store.crud_mixins._write import (
    RequirementStoreWriteMixin,
)


class RequirementStoreCrudMixin(RequirementStoreWriteMixin, RequirementStoreReadMixin):
    """upsert + list + get + update + reset_all_is_generated + delete。"""

    pass
