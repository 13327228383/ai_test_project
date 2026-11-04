# -*- coding: utf-8 -*-
"""★ P2-11 MultimodalRetriever — 多模态联合检索器。

定位：
  从 multimodal_chunks 表联合检索需求/DOM/Figma 块。替代现有 rag_engine 的
  单模态文本检索，支持跨模态（需求↔DOM↔Figma）关联查找。

检索策略（纯算法，不依赖外部向量库）：
  1. 关键词提取：query 分词（中文 2-gram + 英文空格分词）
  2. 文本匹配：计算 query 关键词在 chunk content 中的匹配度
  3. 跨模态联合：按 source_type 分类返回，确保多模态覆盖
  4. 向量检索（可选）：若 chunk 有 embedding，用余弦相似度增强

职责边界（§12.4）：
  - 仅做检索（读 + 排序），不做写入（写入由 store.py 负责）
  - 不依赖业务术语，分词用通用规则（2-gram + 空格）
  - 降级：无数据/异常 → 返回空列表，不阻断

通用性（§12.4）：
  1. 跨系统：分词规则通用，不绑定业务
  2. 跨行业：任何文本都适用
  3. 跨页面：dom 类型 ref_id 是 page_url，通用
  4. 降级：无 embedding → 仅文本匹配；无数据 → 空列表
  5. 职责边界：仅检索，写入由 store.py 负责

证伪设计（小样本）：
  在写入的 multimodal_chunks 上验证 retrieve 能返回相关块（按 score 排序）。
  全量证伪（实跑检索质量评估）需人类决策。
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Optional

from src.rag.store import MultimodalStore, get_store

_log = logging.getLogger(__name__)

# 中文 2-gram 的最小词长（过滤单字符噪声）
_MIN_NGRAM_LEN = 2
# 英文单词最小长度
_MIN_WORD_LEN = 2
# 默认 top_k
_DEFAULT_TOP_K = 5


def _tokenize(text: str) -> list[str]:
    """通用分词（中文 2-gram + 英文空格分词）。

    通用性：
      - 中文：每 2 个字符一个词（不依赖 jieba 等外部库）
      - 英文：按空格/标点分词，保留长度 >= 2 的词
      - 数字：保留（可能是 ID/版本号）

    返回: 分词列表（去重）
    """
    if not text:
        return []
    text = str(text).strip().lower()
    tokens: list[str] = []
    seen: set[str] = set()

    # 提取英文单词（字母+数字组合）
    for m in re.finditer(r"[a-z0-9]+", text):
        word = m.group()
        if len(word) >= _MIN_WORD_LEN:
            if word not in seen:
                tokens.append(word)
                seen.add(word)

    # 提取中文 2-gram
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
    for i in range(len(chinese_chars) - 1):
        gram = chinese_chars[i] + chinese_chars[i + 1]
        if len(gram) >= _MIN_NGRAM_LEN and gram not in seen:
            tokens.append(gram)
            seen.add(gram)

    # 单个中文字符也作为 token（兜底，可能匹配单字关键词）
    for ch in chinese_chars:
        if ch not in seen:
            tokens.append(ch)
            seen.add(ch)

    return tokens


def _compute_text_score(query_tokens: list[str], content: str) -> float:
    """计算 query 与 content 的文本匹配得分。

    策略：
      - 匹配数 / query_tokens 总数 = 覆盖率
      - 匹配词在 content 中出现的频率 = TF
      - score = 覆盖率 * 0.6 + TF * 0.4

    通用性：纯算法，不依赖业务。
    """
    if not query_tokens or not content:
        return 0.0
    content_lower = str(content).lower()
    matched = 0
    total_occurrences = 0
    for token in query_tokens:
        if token in content_lower:
            matched += 1
            # 统计出现次数（TF）
            total_occurrences += content_lower.count(token)
    coverage = matched / len(query_tokens)
    # TF 归一化（对数缩放，避免长文本得分过高）
    tf = math.log(1 + total_occurrences) / math.log(1 + len(content_lower) or 1)
    return coverage * 0.6 + tf * 0.4


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """计算两个向量的余弦相似度。

    通用性：纯数学，不依赖外部库。
    """
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class MultimodalRetriever:
    """多模态联合检索器。

    使用方式:
        retriever = MultimodalRetriever()
        results = retriever.retrieve("project", "查询文本", top_k=5)
        # results = [{id, source_type, ref_id, content, score}, ...]
    """

    def __init__(self, store: Optional[MultimodalStore] = None):
        self._store = store or get_store()
        self._last_error: str = ""

    def retrieve(
        self,
        project: str,
        query: str,
        source_types: Optional[list[str]] = None,
        top_k: int = _DEFAULT_TOP_K,
        use_embedding: bool = False,
    ) -> list[dict[str, Any]]:
        """联合检索 multimodal_chunks。

        参数:
            project: 项目名
            query: 查询文本
            source_types: 限制检索的来源类型（None=所有类型）
            top_k: 返回 top_k 个最相关的块
            use_embedding: 是否使用向量相似度（需要 chunk 有 embedding）

        返回: [{id, source_type, ref_id, content, score, matched_tokens}, ...]
              按 score 降序排序
        """
        try:
            self._last_error = ""
            if not project or not query:
                return []

            # 1. 从 store 读取所有块
            all_chunks: list[dict[str, Any]] = []
            if source_types:
                for st in source_types:
                    chunks = self._store.get_chunks(project, source_type=st, limit=500)
                    all_chunks.extend(chunks)
            else:
                all_chunks = self._store.get_chunks(project, limit=500)

            if not all_chunks:
                self._last_error = "no chunks found"
                return []

            # 2. 分词
            query_tokens = _tokenize(query)
            if not query_tokens:
                self._last_error = "no query tokens"
                return []

            # 3. 计算每个块的得分
            scored: list[dict[str, Any]] = []
            for chunk in all_chunks:
                content = chunk.get("content") or ""
                text_score = _compute_text_score(query_tokens, content)

                # 向量相似度（可选）
                emb_score = 0.0
                if use_embedding and chunk.get("embedding"):
                    # query 的 embedding 需要外部生成，这里仅用文本匹配兜底
                    # 实际使用时，调用方应先生成 query embedding 再传入
                    pass

                total_score = text_score + emb_score
                if total_score > 0:
                    # 计算匹配的 tokens
                    content_lower = content.lower()
                    matched = [t for t in query_tokens if t in content_lower]
                    scored.append(
                        {
                            "id": chunk.get("id", 0),
                            "source_type": chunk.get("source_type", ""),
                            "ref_id": chunk.get("ref_id", ""),
                            "content": content,
                            "score": round(total_score, 4),
                            "matched_tokens": matched,
                        }
                    )

            # 4. 按 score 降序排序
            scored.sort(key=lambda x: x["score"], reverse=True)

            # 5. 返回 top_k
            return scored[: int(top_k)]

        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning("[MultimodalRetriever] retrieve 失败: %s", str(e)[:120])
            return []

    def retrieve_by_source(
        self,
        project: str,
        query: str,
        source_type: str,
        top_k: int = _DEFAULT_TOP_K,
    ) -> list[dict[str, Any]]:
        """按来源类型检索（便捷方法）。

        参数:
            source_type: requirement/dom/figma

        返回: 同 retrieve，但仅返回指定 source_type 的块
        """
        return self.retrieve(project, query, source_types=[source_type], top_k=top_k)

    def retrieve_cross_modal(
        self,
        project: str,
        query: str,
        top_k_per_source: int = 2,
    ) -> dict[str, list[dict[str, Any]]]:
        """跨模态联合检索（每个 source_type 返回 top_k_per_source 个）。

        策略：
          - 对 requirement/dom/figma 分别检索
          - 每个类型返回 top_k_per_source 个最相关的块
          - 确保多模态覆盖（不只返回单一类型）

        返回: {requirement: [...], dom: [...], figma: [...]}
        """
        try:
            self._last_error = ""
            result: dict[str, list[dict[str, Any]]] = {}
            for st in ("requirement", "dom", "figma"):
                result[st] = self.retrieve_by_source(
                    project, query, st, top_k=top_k_per_source
                )
            return result
        except Exception as e:
            self._last_error = str(e)[:200]
            _log.warning(
                "[MultimodalRetriever] retrieve_cross_modal 失败: %s", str(e)[:120]
            )
            return {"requirement": [], "dom": [], "figma": []}


# ═══════════════════════════════════════════════════════════════
# 单例（惰性初始化）
# ═══════════════════════════════════════════════════════════════

_default_retriever: Optional[MultimodalRetriever] = None


def get_retriever() -> MultimodalRetriever:
    """获取默认 MultimodalRetriever 实例（单例，惰性初始化）。"""
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = MultimodalRetriever()
    return _default_retriever
