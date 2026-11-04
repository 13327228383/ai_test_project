# -*- coding: utf-8 -*-
"""
搜索核心、query 改写、rerank、BM25

本模块为 facade，实际实现拆分到 search_mixin_mixins/ 子包中。
"""

import logging

from .search_mixin_mixins._query_rewrite import SearchQueryRewriteMixin
from .search_mixin_mixins._rerank import SearchRerankMixin
from .search_mixin_mixins._vector import SearchVectorMixin
from .search_mixin_mixins._hybrid import SearchHybridMixin
from .search_mixin_mixins._bm25 import SearchBm25Mixin
from .search_mixin_mixins._search import SearchEntryMixin

_log = logging.getLogger("rag_engine")


class SearchMixin(
    SearchQueryRewriteMixin,
    SearchRerankMixin,
    SearchVectorMixin,
    SearchHybridMixin,
    SearchBm25Mixin,
    SearchEntryMixin,
):
    """搜索核心、query 改写、rerank、BM25 相关方法。"""

    pass
