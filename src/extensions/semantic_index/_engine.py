# -*- coding: utf-8 -*-
from __future__ import annotations

"""引擎管理：懒加载 sentence-transformers 模型 & RagEngine 单例。"""
import logging
import threading
from pathlib import Path
from typing import Any

_log = logging.getLogger("semantic_index")

_shared_engine = None
_local_model = None
_local_model_failed = False  # ★ 缓存失败状态，避免每次 embed() 都重试全流程
_model_lock = threading.Lock()  # ★ 防止 ThreadPoolExecutor 多 worker 同时加载模型


def _ensure_local_model() -> Any | None:
    """★ 磁盘 SentenceTransformer 模型懒加载（仅 embed_provider=disk 时加载）。

    ★ 单一入口：config.disk_embed_model（前端配置入库）
    ★ 无硬编码模型名：未配置时扫描磁盘目录自动选择（检测，非兜底）
    ★ 类型职责清晰：仅在 provider=disk 时工作，不与 ollama/cloud 互相兜底
    ★ 双重检查锁定：防止 ThreadPoolExecutor 多 worker 同时加载模型
    显式禁用：RAG_DISABLE_SENTENCE_TRANSFORMERS=1 强制回退 ngram。
    """
    global _local_model, _local_model_failed
    if _local_model is not None:
        return _local_model
    if _local_model_failed:  # ★ 已失败过，不再重试，避免每次调用刷日志
        return None

    # ★ provider 检查：仅 disk 模式才加载磁盘模型（避免非 disk 模式浪费资源）
    try:
        from src.core.config_loader import config as _cfg

        if str(getattr(_cfg, "embed_provider", "")) != "disk":
            return None
    except Exception as _e:
        _log.debug("磁盘嵌入 provider 配置读取失败: %s", _e, exc_info=True)
        return None

    # ★ 双重检查锁定：防止 ThreadPoolExecutor 10 个 worker 同时加载模型，
    #    导致 "Cannot copy out of meta tensor" 错误 + 30-50s 卡顿
    with _model_lock:
        if _local_model is not None:
            return _local_model
        if _local_model_failed:
            return None

        import os

        # ★ opt-out：显式禁用
        if os.environ.get("RAG_DISABLE_SENTENCE_TRANSFORMERS", "").strip() == "1":
            _log.info(
                "[Embedding] 已通过 RAG_DISABLE_SENTENCE_TRANSFORMERS 禁用，使用 ngram fallback"
            )
            return None

        try:
            # ★ 必须在 import 前设置：huggingface_hub 在 import 时就探测网络
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            from sentence_transformers import SentenceTransformer
        except ImportError:
            _log.info(
                "[Embedding] sentence-transformers 未安装，使用 ngram fallback"
                "（pip install sentence-transformers 可获得更精准的语义匹配）"
            )
            return None

        # ★ 固定缓存路径：项目根目录/models/sentence-transformers/
        _cache_dir = str(
            Path(__file__).resolve().parent.parent.parent.parent
            / "models"
            / "sentence-transformers"
        )
        Path(_cache_dir).mkdir(parents=True, exist_ok=True)

        # ★ 单一入口：读取前端配置的磁盘模型名（无硬编码默认值）
        model_name = str(getattr(_cfg, "disk_embed_model", "") or "")
        if not model_name:
            # 未配置：扫描磁盘目录自动选择第一个可用模型（检测，非兜底）
            try:
                available = _cfg.list_disk_embed_models()
            except Exception:
                available = []
            if not available:
                _log.warning("[Embedding] 磁盘嵌入模型未配置且目录无可用模型")
                _local_model_failed = True
                return None
            # 优先选 multilingual 模型（中文场景更优）
            for m in available:
                if "multilingual" in m.get("full_name", "").lower():
                    model_name = m["full_name"]
                    break
            if not model_name:
                model_name = available[0]["full_name"]
            _log.info("[Embedding] 未配置 disk_embed_model，自动选择: %s", model_name)

        # ★ 优先从本地缓存加载（local_files_only=True，不联网）
        try:
            _local_model = SentenceTransformer(
                model_name,
                cache_folder=_cache_dir,
                local_files_only=True,
                device="cpu",  # ★ 显式 CPU，避免 safetensors mmap meta tensor 错误
            )
            _log.info("[Embedding] 磁盘模型加载成功: %s", model_name)
            return _local_model
        except Exception as _e:
            _log.warning(
                "[Embedding] 磁盘模型加载失败: %s (模型名: %s)，使用 ngram fallback",
                str(_e)[:80],
                model_name,
            )
            _local_model_failed = True  # ★ 缓存失败状态，后续调用不再重试
            return None


def _get_engine():
    """获取 RagEngine 实例（不依赖磁盘路径）。"""
    global _shared_engine
    if _shared_engine is None:
        try:
            from src.extensions.rag_engine import RagEngine

            _shared_engine = RagEngine()
        except Exception as e:
            _log.warning("rag_engine 不可用 (%s)，回退 hash", str(e)[:60])
            _shared_engine = None
    return _shared_engine


def _normalize(vec: list[float]) -> list[float]:
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec] if norm > 0 else vec


def _try_embed_ollama(text: str) -> list[float] | None:
    """通过本地 Ollama /api/embed 获取向量。

    ★ 尊重 embed_provider 配置：仅当 provider=ollama 时调用
    ★ 无硬编码默认值：模型名/URL 均从 config 读取（config 已走 user_credentials）
    异常静默返回 None，由上游继续降级到 ngram fallback。
    """
    try:
        from src.core.config_loader import config as _cfg

        # ★ provider 检查：cloud 模式跳过 Ollama 直连（避免兜底切换）
        if str(getattr(_cfg, "embed_provider", "ollama")) != "ollama":
            return None
        api_base = str(getattr(_cfg, "ollama_api_base", "")).rstrip("/")
        embed_model = str(getattr(_cfg, "ollama_embed_model", ""))
        enabled = bool(getattr(_cfg, "ollama_enabled", True))
    except Exception as _e:
        _log.debug("Ollama 嵌入配置读取失败: %s", _e, exc_info=True)
        return None
    if not enabled or not api_base or not embed_model:
        return None
    import requests as _requests

    try:
        resp = _requests.post(
            f"{api_base}/api/embed",
            json={"model": embed_model, "input": text},
            timeout=3,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        embeddings = data.get("embeddings", [])
        if not embeddings or len(embeddings[0]) == 0:
            return None
        vec = [float(v) for v in embeddings[0]]
        # ★ 对齐维度：Ollama 模型输出维度可能 > 384，截断到 384
        if len(vec) >= 384:
            vec = vec[:384]
        elif len(vec) >= 128:
            vec = vec + [0.0] * (384 - len(vec))
        else:
            vec = vec + [0.0] * (128 - len(vec))
        return _normalize(vec)
    except Exception as _e:
        _log.debug("Ollama 嵌入请求失败: %s", _e, exc_info=True)
        return None
