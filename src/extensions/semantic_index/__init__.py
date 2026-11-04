# -*- coding: utf-8 -*-
"""语义嵌入工具：实时向量生成 + 相似度计算。
无状态设计，不依赖任何存储表。向量持久化由 rag_chunks 统一承载。"""
from ._engine import (
    _ensure_local_model,
    _get_engine,
    _normalize,
    _shared_engine,
    _local_model,
    _try_embed_ollama,
)
from ._ngram import _ngram_fallback, _NGRAM_SIZES, _NGRAM_VEC_DIM
from ._index import (
    embed,
    cosine,
    similarity,
    get_model_status,
)
