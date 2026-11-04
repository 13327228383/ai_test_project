# -*- coding: utf-8 -*-
"""
Matcher Phase2 Mixin: 功能点语义匹配
"""

import re
import math
from collections import Counter
from difflib import SequenceMatcher as _SeqMatcher


import logging

_log = logging.getLogger(__name__)


class _MatcherPhase2Mixin:
    """提供 Phase2 语义匹配相关方法。"""

    # ═══════════════════════════════════════════
    # Phase 2: 功能点语义匹配
    # ═══════════════════════════════════════════

    def _compute_feature_similarity(self, feature_text: str, intent: dict) -> float:
        """
        计算功能点文本与测试用例内容的语义相似度。
        """
        title = str(intent.get("title") or "")
        steps = intent.get("case_actions_json") or []
        if isinstance(steps, list):
            steps_text = " ".join(
                s.get("description", str(s)) if isinstance(s, dict) else str(s)
                for s in steps
            )
        else:
            steps_text = str(steps)

        intent_text = f"{title} {steps_text}"[:1000]

        if not feature_text or not intent_text.strip():
            return 0.0

        # embedding 路径
        if self._use_embeddings:
            try:
                feat_vec = self._embed_text(feature_text)
                intent_vec = self._embed_text(intent_text)
                if feat_vec and intent_vec and len(feat_vec) == len(intent_vec):
                    sim = self._cosine_fn(feat_vec, intent_vec)
                    return max(0.0, min(1.0, sim))
            except Exception as _e:
                _log.debug("未知: %s", _e, exc_info=True)

        # ★ P3-5: TF-IDF回退（比关键词更精确）
        tfidf_sim = self._tfidf_similarity(feature_text, intent_text)
        if tfidf_sim > 0.3:
            return tfidf_sim

        # 关键词回退（保底）
        return self._keyword_similarity(feature_text, intent_text)

    @staticmethod
    def _keyword_similarity(text_a: str, text_b: str) -> float:
        """
        基于 SequenceMatcher + 核心词包含的相似度（embedding 不可用时的回退方案）。
        """
        if not text_a or not text_b:
            return 0.0

        def clean(s):
            s = re.sub(r"[（(][^）)]*[）)]", "", s)
            s = re.sub(r'[，。！？、；："\s]+', "", s)
            return s.lower()

        ta = clean(text_a)
        tb = clean(text_b)
        if not ta or not tb:
            return 0.0

        sm = _SeqMatcher(None, ta, tb)
        seq_ratio = sm.ratio()

        core_words = set()
        for n in [2, 3]:
            for i in range(len(ta) - n + 1):
                chunk = ta[i : i + n]
                if re.search(r"[一-鿿]", chunk):
                    core_words.add(chunk)

        if core_words:
            matched = sum(1 for kw in core_words if kw in tb)
            word_ratio = matched / len(core_words)
        else:
            word_ratio = 0.0

        return 0.7 * seq_ratio + 0.3 * word_ratio

    @staticmethod
    def _tfidf_similarity(text_a: str, text_b: str) -> float:
        """
        ★ P3-5: 基于 TF-IDF 的余弦相似度。
        比关键词匹配更精确，比 Embedding 更轻量（无需AI API）。
        """
        if not text_a or not text_b:
            return 0.0

        def tokenize(text):
            """分词：提取中文和英文单词（2字/词以上）。"""
            # 中文：连续2-4个中文字符
            zh_tokens = re.findall(r"[一-鿿]{2,4}", text)
            # 英文：单词
            en_tokens = re.findall(r"[a-zA-Z]{2,}", text.lower())
            return zh_tokens + en_tokens

        tokens_a = tokenize(text_a)
        tokens_b = tokenize(text_b)

        if not tokens_a or not tokens_b:
            return 0.0

        all_tokens = set(tokens_a + tokens_b)
        if not all_tokens:
            return 0.0

        # 简化的IDF（单文档集合，所有词权重相同）
        idf = {t: 1.0 for t in all_tokens}

        def tfidf_vector(tokens):
            tf = Counter(tokens)
            total = len(tokens)
            vec = {}
            for term, count in tf.items():
                vec[term] = (count / total) * idf.get(term, 1.0)
            return vec

        vec_a = tfidf_vector(tokens_a)
        vec_b = tfidf_vector(tokens_b)

        # 余弦相似度
        dot = sum(vec_a.get(t, 0) * vec_b.get(t, 0) for t in all_tokens)
        norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
        norm_b = math.sqrt(sum(v * v for v in vec_b.values()))

        if norm_a == 0 or norm_b == 0:
            return 0.0
        return max(0.0, min(1.0, dot / (norm_a * norm_b)))
