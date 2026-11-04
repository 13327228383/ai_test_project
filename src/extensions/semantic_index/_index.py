# -*- coding: utf-8 -*-
from __future__ import annotations

"""语义嵌入工具函数：实时向量生成与相似度计算（无状态，不依赖存储表）。"""
import logging, functools
from pathlib import Path
from typing import Any

from ._engine import _ensure_local_model, _get_engine, _try_embed_ollama
from ._ngram import _ngram_fallback

_log = logging.getLogger("semantic_index")


@functools.lru_cache(maxsize=512)
def embed(text: str) -> list[float] | None:
    """★ 将文本编码为向量。严格按 embed_provider 路由（无兜底切换）。
    - disk → _ensure_local_model（磁盘 SentenceTransformer）
    - ollama → _try_embed_ollama（Ollama /api/embed）
    - cloud → _get_engine()._embed_text（走 RAG 引擎 → 云端 API）
    指定 provider 调用失败 → ngram fallback（搜索质量保障，不切其他 provider）
    """
    if not text or not str(text).strip():
        return None
    # ★ opt-out：显式禁用 → ngram fallback
    import os as _embed_os

    if _embed_os.environ.get("RAG_DISABLE_SENTENCE_TRANSFORMERS", "").strip() == "1":
        return _ngram_fallback(str(text))

    # ★ 严格按 embed_provider 路由（单一入口，无兜底切换）
    try:
        from src.core.config_loader import config as _cfg

        provider = str(getattr(_cfg, "embed_provider", ""))
    except Exception:
        provider = ""

    vec = None
    if provider == "disk":
        # 磁盘 SentenceTransformer（384 维原生，已归一化）
        model = _ensure_local_model()
        if model is not None:
            vec = model.encode(str(text), normalize_embeddings=True).tolist()
    elif provider == "ollama":
        # Ollama 本地 embedding（/api/embed，无需 PyTorch 进程内加载）
        vec = _try_embed_ollama(str(text))
    elif provider == "cloud":
        # 云端 API（走 RAG 引擎的 _embed_text，它已做严格 cloud 路由）
        engine = _get_engine()
        if engine is not None:
            vec = engine._embed_text(text)
    else:
        # 未配置 provider → ngram fallback
        return _ngram_fallback(str(text))

    if vec is not None:
        return vec[:384] if len(vec) >= 128 else vec + [0.0] * (128 - len(vec))
    # 指定 provider 调用失败 → ngram fallback（搜索质量保障，不切其他 provider）
    return _ngram_fallback(str(text))


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（也做点积，对已归一化向量两者等价）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    engine = _get_engine()
    if engine is not None:
        return engine._cosine_sim(a, b)
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def similarity(text_a: str, text_b: str) -> float:
    """实时计算两段文本的语义相似度（不依赖存储，每次实时 embed）。"""
    va, vb = embed(text_a), embed(text_b)
    return cosine(va, vb) if va and vb else 0.0


def get_model_status() -> dict[str, Any]:
    """返回当前向量模型状态，供健康检查 API 使用。

    ★ 2026-07-13: 不再主动触发模型加载（避免阻塞 metrics 接口 14s）。
    仅读取已加载状态；模型在 Step3 真正需要时由 embed() 触发懒加载，
    或由 app.py 启动预热线程提前加载。
    ★ 严格按 embed_provider 报告状态（无硬编码模型名）。
    """
    import os

    try:
        from src.core.config_loader import config as _cfg

        provider = str(getattr(_cfg, "embed_provider", ""))
        disk_model_name = str(getattr(_cfg, "disk_embed_model", "") or "")
    except Exception:
        provider = ""
        disk_model_name = ""

    # ★ 不触发加载，仅读取已加载状态（_local_model 已加载则为 None 之外的对象）
    from src.extensions.semantic_index._engine import _local_model, _local_model_failed

    model = _local_model
    engine = _get_engine()
    _cache_dir = str(
        Path(__file__).resolve().parent.parent.parent.parent
        / "models"
        / "sentence-transformers"
    )

    # ★ 根据 provider 报告模式（无硬编码模型名）
    if model is not None:
        mode = "disk_transformer"
        model_name = disk_model_name or getattr(model, "model_name", "") or "disk_model"
    elif provider == "ollama":
        mode = "ollama_embed"
        model_name = (
            str(getattr(_cfg, "ollama_embed_model", "") or "") if provider else ""
        )
    elif provider == "cloud":
        mode = "cloud_api" if engine is not None else "ngram_fallback"
        model_name = str(getattr(_cfg, "embed_model", "") or "") if provider else ""
    elif provider == "disk":
        # disk 模式但模型未加载（预热未完成或加载失败）
        mode = "disk_transformer" if not _local_model_failed else "ngram_fallback"
        model_name = disk_model_name
    else:
        mode = "ngram_fallback"
        model_name = None

    return {
        "mode": mode,
        "model": model_name,
        "provider": provider,
        "dim": 384 if model else 0,
        "cache_path": str(_cache_dir) if model else None,
        "is_semantic": model is not None,
    }
