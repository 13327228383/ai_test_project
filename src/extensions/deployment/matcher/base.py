# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Matcher Base Mixin: 初始化 + embedding + 代理静态方法
"""

import math
import logging

_log = logging.getLogger(__name__)

from src.extensions.deployment.parser import (
    parse_markdown_sections,
    detect_transfer_scope_section,
    parse_table,
    filter_by_version,
    list_versions,
    extract_keywords_static,
    cosine_sim_static,
)


class _MatcherBaseMixin:
    """提供 __init__、embedding 初始化、以及委托给 parser 的代理静态方法。"""

    def __init__(self):
        self._use_embeddings = False
        self._embed_fn = None
        self._ai_brain = None
        # ★ 安全默认值：静态回退函数，不依赖外部 API
        self._keyword_fn = extract_keywords_static
        self._cosine_fn = cosine_sim_static

    def initialize(self):
        """延迟初始化：尝试连接 embedding API。不可用时静默回退。"""
        self._setup_embedding()

    # ═══════════════════════════════════════════
    # 初始化 embedding
    # ═══════════════════════════════════════════

    def _setup_embedding(self):
        """尝试初始化 embedding 能力。不可用时静默回退。"""
        # 保存关键词提取（永远可用）
        self._keyword_fn = extract_keywords_static

        # 尝试加载 embedding
        try:
            from src.ai.ai_client import ai_brain

            test_vec = self._try_embed(ai_brain, "测试")
            if test_vec and len(test_vec) > 0:
                self._use_embeddings = True
                self._ai_brain = ai_brain
                self._cosine_fn = cosine_sim_static
                _log.info("  [DeploymentMatcher] Embedding API 可用")
                return
        except Exception as _e:
            _log.debug("import + ai + ai: %s", _e, exc_info=True)

        self._use_embeddings = False
        _log.info("  [DeploymentMatcher] Embedding 不可用，使用关键词匹配")

    def _try_embed(self, ai_brain, text: str) -> list[float] | None:
        """调用 embedding API。"""
        try:
            result = ai_brain._call_api(
                system_prompt="",
                user_message=text[:2000],
                temperature=0.0,
                max_tokens=1,
                caller_name="DeploymentMatcher",
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
        except Exception as _e:
            _log.debug("token: %s", _e, exc_info=True)
        return None

    def _embed_text(self, text: str) -> list[float] | None:
        """获取文本向量。"""
        if not text or not text.strip():
            return None
        # ★ 防御：_ai_brain 仅在 _setup_embedding 成功时赋值
        if not hasattr(self, "_ai_brain") or self._ai_brain is None:
            return self._fallback_embed(text)
        try:
            return self._try_embed(self._ai_brain, text)
        except Exception:
            return self._fallback_embed(text)

    def _fallback_embed(self, text: str) -> list[float]:
        """回退：字符 n-gram 哈希向量（不依赖外部 API）。"""
        keywords = self._keyword_fn(text)
        if not keywords:
            return [0.0] * 128
        vec = [0.0] * 128
        for kw in keywords:
            h = hash(kw) % 128
            vec[h] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    # ═══════════════════════════════════════════
    # 代理方法 — 委托给 parser 模块函数
    # ═══════════════════════════════════════════

    _parse_markdown_sections = staticmethod(parse_markdown_sections)
    _detect_transfer_scope_section = staticmethod(detect_transfer_scope_section)
    _parse_table = staticmethod(parse_table)
    _filter_by_version = staticmethod(filter_by_version)
    _list_versions = staticmethod(list_versions)
    _extract_keywords_static = staticmethod(extract_keywords_static)
    _cosine_sim_static = staticmethod(cosine_sim_static)
