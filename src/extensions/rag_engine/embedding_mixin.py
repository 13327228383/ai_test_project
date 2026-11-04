# -*- coding: utf-8 -*-
from __future__ import annotations
from src.core.guarded import guard

"""
向量嵌入相关方法
"""

import math
import logging
from pathlib import Path

_log = logging.getLogger("rag_engine")


class EmbeddingMixin:
    """向量嵌入构建与计算相关方法。"""

    # ═══════════════════════════════════════════
    # Embedding
    # ═══════════════════════════════════════════

    def _try_build_embeddings(self, project: str = "", version_label: str = "") -> None:
        """尝试为所有 chunk 构建向量嵌入。

        project/version_label 由 build_index 传入：增量复用的旧向量只能来自同一
        版本分区，跨版本复用会把别的版本的内容语义带进来。
        """
        if not self._chunks:
            return

        # ★ G4 (2026-07-10): 增量嵌入 — 加载旧 chapter_hash → vector 映射
        #   相同 chapter_hash 的 chunk 复用旧向量，跳过嵌入调用（省 70%+ 嵌入计算）
        _hash_to_vec = self._load_chapter_hash_vectors(project, version_label)
        _reused = 0

        # 先试一个 chunk
        preview = getattr(self, "_EMBED_CHUNK_PREVIEW", 500)
        test_text = self._chunks[0]["text"][:preview]
        test_vec = self._embed_text(test_text)

        if test_vec and len(test_vec) > 0:
            self._use_embeddings = True
            _log.info("  [RagEngine] 向量嵌入可用，构建向量索引...")
            log_interval = getattr(self, "_EMBED_LOG_INTERVAL", 50)
            max_len = getattr(self, "_EMBED_CHUNK_MAX_LEN", 1000)
            for i, c in enumerate(self._chunks):
                if i % log_interval == 0 and i > 0:
                    _log.info("    ... %d/%d", i, len(self._chunks))
                # ★ G4: chapter_hash 命中 → 复用旧向量，跳过嵌入
                _ch = c.get("chapter_hash", "")
                if _ch and _ch in _hash_to_vec:
                    c["_vector"] = _hash_to_vec[_ch]
                    _reused += 1
                    continue
                vec = self._embed_text(c["text"][:max_len])
                if vec:
                    c["_vector"] = vec
            if _reused > 0:
                _log.info(
                    "  [RagEngine] ★ 增量嵌入: 复用 %d/%d 个旧向量，新增嵌入 %d 个",
                    _reused,
                    len(self._chunks),
                    len(self._chunks) - _reused,
                )
            # ★ H4-5: 记录哈希复用率指标（含 0 复用场景）
            with guard("_try_build_embeddings: from src.backend.state import _threa", log=_log):
                from src.backend.state import _thread_run_id
                from src.core.config_loader import config as _cfg
                from src.extensions.metrics_collector import metrics_collector

                _rid = getattr(_thread_run_id, "value", "") or ""
                if _rid:
                    metrics_collector.record_hash_reuse(
                        _cfg.project_name, _rid, _reused, len(self._chunks)
                    )
        else:
            self._use_embeddings = False
            _log.info("  [RagEngine] 向量嵌入不可用，使用 BM25 关键词检索")

    def _load_chapter_hash_vectors(
        self, project: str = "", version_label: str = ""
    ) -> dict:
        """★ G4: 从 DB 加载同一「项目 + 版本」分区的 chapter_hash → vector 映射。

        只加载有 chapter_hash 且有向量的旧 chunk，用于新 chunk 复用。
        project/version_label 为空时无法确定分区，返回空映射（不复用任何跨版本向量）。
        """
        _hash_map = {}
        with guard("_load_chapter_hash_vectors: project = project or", log=_log):
            project = project or self._get_project_name()
            version_label = version_label or self._get_project_version()
            if not project or not version_label:
                _log.warning(
                    "[RagEngine] 项目(%s)或版本(%s)上下文缺失，跳过旧向量复用",
                    project or "?",
                    version_label or "?",
                )
                return _hash_map
            scope_type, scope_id = self._project_scope(project)
            from src.core.db_manager import get_connection

            conn = get_connection()
            try:
                rows = (
                    conn.execute(
                        "SELECT chapter_hash, vector_blob, vector_json, embed_dim "
                        "FROM rag_chunks WHERE scope_type=%s AND scope_id=%s "
                        "AND version_label=%s AND is_active=1 "
                        "AND chapter_hash != '' AND embed_dim > 0",
                        (scope_type, scope_id, version_label),
                    ).fetchall()
                    or []
                )
                for row in rows:
                    ch = row.get("chapter_hash", "")
                    if not ch:
                        continue
                    vec = None
                    if row.get("vector_blob"):
                        try:
                            vec = self._blob_to_vector(
                                row["vector_blob"], dim=row.get("embed_dim", 0)
                            )
                        except Exception as _e:
                            _log.debug(
                                "_blob_to_vector 解析失败: %s", _e, exc_info=True
                            )
                    if not vec and row.get("vector_json"):
                        try:
                            import json

                            vec = json.loads(row["vector_json"])
                        except Exception as _e:
                            _log.debug("vector_json 解析失败: %s", _e, exc_info=True)
                    if vec:
                        _hash_map[ch] = vec
            finally:
                conn.close()
        return _hash_map

    def _embed_text(self, text: str) -> list[float] | None:
        """★ 严格按 embed_provider 路由（无兜底切换，类型职责清晰）。
        - disk → 仅 _try_embed_disk（磁盘 SentenceTransformer）
        - ollama → 仅 _try_embed_ollama（Ollama /api/embed）
        - cloud → 仅 _try_embed_api（云端 API）
        指定 provider 调用失败 → ngram fallback（保证搜索可用性，但不切到其他 provider）
        """
        if not text or not text.strip():
            return None
        max_len = getattr(self, "_EMBED_TEXT_MAX_LEN", 2000)
        truncated = text[:max_len]

        # ★ 严格按 provider 路由（单一入口，无兜底切换）
        try:
            from src.core.config_loader import config as _cfg

            provider = str(getattr(_cfg, "embed_provider", ""))
        except Exception:
            provider = ""

        if provider == "disk":
            vec = self._try_embed_disk(truncated)
        elif provider == "ollama":
            vec = self._try_embed_ollama(truncated)
        elif provider == "cloud":
            vec = self._try_embed_api(truncated)
        else:
            # 未配置 provider → ngram fallback
            return self._fallback_embed(truncated)

        if vec is not None:
            return vec
        # 指定 provider 调用失败 → ngram fallback（搜索质量保障，不切其他 provider）
        return self._fallback_embed(truncated)

    def _try_embed_disk(self, text: str) -> list[float] | None:
        """★ 磁盘 SentenceTransformer 模型嵌入（仅 embed_provider=disk 时调用）。
        ★ 单一入口：config.disk_embed_model（前端配置入库）
        ★ 无硬编码候选列表：未配置时扫描磁盘目录自动选择（检测，非兜底）
        ★ 类型职责清晰：仅在 provider=disk 时工作，不与 ollama/cloud 互相兜底
        """
        import os as _os

        _os.environ.setdefault("HF_HUB_OFFLINE", "1")
        _os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            return None
        # ★ 缓存失败状态，避免每次 embed 都重试
        if getattr(self, "_disk_st_model_failed", False):
            return None
        try:
            if not hasattr(self, "_disk_st_model") or self._disk_st_model is None:
                from src.core.config_loader import config as _cfg

                # ★ 单一入口：读取前端配置的磁盘模型名
                model_name = str(getattr(_cfg, "disk_embed_model", "") or "")
                if not model_name:
                    # 未配置：扫描磁盘目录自动选择第一个可用模型（检测，非兜底）
                    try:
                        available = _cfg.list_disk_embed_models()
                    except Exception:
                        available = []
                    if not available:
                        _log.warning("[RagEngine] 磁盘嵌入模型未配置且目录无可用模型")
                        self._disk_st_model_failed = True
                        return None
                    # 优先选 multilingual 模型（中文场景更优）
                    for m in available:
                        if "multilingual" in m.get("full_name", "").lower():
                            model_name = m["full_name"]
                            break
                    if not model_name:
                        model_name = available[0]["full_name"]
                    _log.info(
                        "[RagEngine] 未配置 disk_embed_model，自动选择: %s", model_name
                    )

                _cache_dir = str(
                    Path(__file__).resolve().parent.parent.parent.parent
                    / "models"
                    / "sentence-transformers"
                )
                Path(_cache_dir).mkdir(parents=True, exist_ok=True)

                # ★ 优先从本地缓存加载（local_files_only=True，不联网）
                try:
                    self._disk_st_model = SentenceTransformer(
                        model_name,
                        cache_folder=_cache_dir,
                        local_files_only=True,
                        device="cpu",
                    )
                    _log.info("[RagEngine] 磁盘模型加载成功: %s", model_name)
                except Exception as _e:
                    _log.warning(
                        "[RagEngine] 磁盘模型加载失败: %s (模型名: %s)",
                        str(_e)[:80],
                        model_name,
                    )
                    self._disk_st_model_failed = True
                    return None
            return self._disk_st_model.encode(text, normalize_embeddings=True).tolist()
        except Exception as e:
            _log.debug("[RagEngine] 磁盘 embedding 失败: %s", str(e)[:100])
            return None

    def _try_embed_ollama(self, text: str) -> list[float] | None:
        """通过本地 Ollama /api/embed 获取向量（独立于 PyTorch 进程内加载）。

        ★ 尊重 embed_provider 配置：仅当 provider=ollama 时调用
        ★ 无硬编码默认值：模型名/URL 均从 config 读取（config 已走 user_credentials）
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
                timeout=30,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            embeddings = data.get("embeddings", [])
            if not embeddings or len(embeddings[0]) == 0:
                return None
            vec = [float(v) for v in embeddings[0]]
            # ★ 归一化（与 _try_embed_local 行为对齐）
            norm = math.sqrt(sum(v * v for v in vec))
            return [v / norm for v in vec] if norm > 0 else vec
        except Exception as _e:
            _log.debug("Ollama 嵌入请求失败: %s", _e, exc_info=True)
            return None

    def _try_embed_api(self, text: str) -> list[float] | None:
        with guard("_try_embed_api: from src.ai.ai_client import ai_brai", log=_log):
            from src.ai.ai_client import ai_brain

            result = ai_brain._call_api(
                system_prompt="",
                user_message=text,
                temperature=0.0,
                max_tokens=1,
                caller_name="RAG嵌入",
                task_type="embedding",
            )
            if result.get("success"):
                data = result.get("data", {})
                if isinstance(data, dict):
                    vec = data.get("embedding", data.get("vector", data.get("data")))
                    if (
                        isinstance(vec, list)
                        and len(vec) > 0
                        and isinstance(vec[0], (int, float))
                    ):
                        return [float(v) for v in vec]
        return None

    def _fallback_embed(self, text: str) -> list[float]:
        """
        回退方案：基于字符 n-gram 的简单向量。
        不为精确匹配设计，但能提供基本的相似度排序。
        ★ 固定128维，与embedding向量维度统一。
        """
        keywords = self._extract_keywords(text)
        embed_dim = getattr(self, "_EMBED_DIM", 128)
        vec = [0.0] * embed_dim
        for kw in keywords:
            # NOTE: hash(kw) % dim 存在碰撞风险——不同关键词可能映射到同一维度。
            # 作为 embedding API 不可用时的回退方案，此近似排序可接受；
            # 若需精确语义检索，应确保 embedding API 可用。
            h = hash(kw) % embed_dim
            vec[h] += 1.0

        # 归一化
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        """余弦相似度。"""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0
