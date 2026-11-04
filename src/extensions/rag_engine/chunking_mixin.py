# -*- coding: utf-8 -*-
"""
文档分块、格式化、关键词提取、去重、智能分段
"""

import logging
from .chunking_mixin_mixins._keywords import ChunkingKeywordsMixin
from .chunking_mixin_mixins._dedup import ChunkingDedupMixin
from .chunking_mixin_mixins._parse import ChunkingParseMixin
from .chunking_mixin_mixins._format import ChunkingFormatMixin
from .chunking_mixin_mixins._upload import ChunkingUploadMixin
from .chunking_mixin_mixins._segment import SmartSegmentMixin

_log = logging.getLogger("rag_engine")


class ChunkingMixin(
    ChunkingKeywordsMixin,
    ChunkingDedupMixin,
    ChunkingParseMixin,
    ChunkingFormatMixin,
    ChunkingUploadMixin,
    SmartSegmentMixin,
):
    """文档分块、格式化、关键词提取、去重、智能分段"""

    pass
