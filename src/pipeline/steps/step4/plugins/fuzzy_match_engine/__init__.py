# -*- coding: utf-8 -*-
from __future__ import annotations

"""
fuzzy_match_engine — 能力缓存模糊语义匹配引擎（包入口）
=========================================================
当 capability_cache.make_capability_key 的精确 SHA256 匹配失败时，
本模块用 Jaccard 相似度 + 页面签名软匹配，从缓存表中召回相似能力，
提升 cache 命中率。

核心思路：
1. 启动时（或每 100 次命中 / 手动调用）从 MySQL 表 capability_cache 拉取
   所有 is_valid=1 的条目，构建内存索引 {capability_key: {tokens, sig_hash, ...}}。
2. 对当前意图文本做分词（中文 2-gram + 英文词 + 停用词过滤），计算与各缓存
   条目的 Jaccard 相似度。
3. 若调用方提供了当前页面签名，则仅对 signature 匹配的条目做二次筛选。
4. 返回最多 top-K 的候选列表，供调用方决策。

【2026-06-18 重构】原 530 行单文件已按职责拆为：
  tokenizer.py  — 停用词 + 语义组 + tokenize + _concept_tokens_only / _char_tokens_only / _semantic_tokens
  similarity.py — jaccard + semantic_similarity
  metrics.py    — _record_fuzzy_metric (KPI 埋点)
  __init__.py   — FuzzyMatchEngine 类 + 单例 + 便捷函数 fuzzy_match / rebuild_index

使用示例:
    from src.pipeline.steps.step4.plugins.fuzzy_match_engine import fuzzy_match_engine
    candidates = fuzzy_match_engine.fuzzy_match(
        current_intent_text="登录系统",
        current_page_url="/login",
        current_page_signature_hash="abc123",
        threshold=0.55,
    )
"""

import logging
import time
from typing import Any, Set

# ── re-export：保持调用方接口完全不变 ──
from .tokenizer import (
    _STOPWORDS,
    _SEMANTIC_GROUPS,
    _semantic_tokens,
    tokenize,
    _concept_tokens_only,
    _char_tokens_only,
)
from .similarity import jaccard, semantic_similarity
from .metrics import _record_fuzzy_metric

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 主类 FuzzyMatchEngine
# ---------------------------------------------------------------------------
class FuzzyMatchEngine:
    """能力缓存的模糊语义匹配引擎（内存索引 + MySQL 懒加载）。"""

    _TABLE = "capability_cache"
    _REBUILD_EVERY_HITS = 100  # 每命中 100 次自动重建一次
    # ★ P1: 默认阈值走 pipeline_config.fuzzy_match_default_threshold
    _DEFAULT_THRESHOLD: float = None  # 实际值通过 _resolve_default_threshold() 动态读取
    _DEFAULT_TOP_K = 3

    @classmethod
    def _resolve_default_threshold(cls) -> float:
        """★ P1: 动态读取 fuzzy_match 默认阈值(支持 env 覆盖)。"""
        if cls._DEFAULT_THRESHOLD is not None:
            return cls._DEFAULT_THRESHOLD
        try:
            from src.core.pipeline_config import pipeline_config

            return float(pipeline_config.fuzzy_match_default_threshold)
        except Exception as _e:
            _log.debug(
                "[__init__] _resolve_default_threshold 异常: %s", _e, exc_info=True
            )
            return 0.55

    def __init__(self):
        # 内存索引: capability_key -> {tokens_set, page_signature_hash, name, case_norm, hit_count, success_count}
        self._index: dict[str, dict[str, Any]] = {}
        self._built = False  # 是否已至少构建过一次
        self._last_rebuild_at = 0.0  # 最近一次重建时间戳
        self._last_rebuild_rows = 0  # 最近一次加载行数
        self._hits_since_rebuild = 0  # 自重建以来的命中次数（用于触发重建）
        _log.info("FuzzyMatchEngine 已创建，等待懒加载索引")

    # ------------------------------------------------------------------
    # 索引构建
    # ------------------------------------------------------------------
    def _conn(self):
        """延迟导入 DB，避免 import 本文件时触发 MySQL 连接。"""
        try:
            from src.core.db_manager import get_connection

            return get_connection()
        except Exception as e:
            _log.warning("FuzzyMatchEngine 无法获取 DB 连接: %s", e)
            return None

    def rebuild_index(self, force: bool = False) -> int:
        """
        从 capability_cache 表重建内存索引。返回加载的条目数。
        """
        conn = None
        try:
            conn = self._conn()
            if conn is None:
                _log.warning("FuzzyMatchEngine: 无 DB 连接，无法重建索引")
                return 0

            rows: list[dict[str, Any]] = []
            try:
                # 仅拉取有效条目，字段够用即可
                cur = conn.execute(
                    f"SELECT capability_key, capability_name, case_normalized, "
                    f"page_signature_hash, hit_count, success_count, is_valid "
                    f"FROM {self._TABLE} WHERE is_valid = 1"
                )
                rows = cur.fetchall() if cur else []
            except Exception as sql_err:
                # 表不存在 / 字段缺失都视为正常，静默处理
                _log.warning("FuzzyMatchEngine: SQL 读取失败: %s", sql_err)
                return 0

            new_index: dict[str, dict[str, Any]] = {}
            for row in rows:
                try:
                    key = str(row.get("capability_key") or "").strip()
                    if not key:
                        continue
                    name = str(row.get("capability_name") or "").strip()
                    case_norm = str(row.get("case_normalized") or "").strip()
                    sig_hash = str(row.get("page_signature_hash") or "").strip()
                    hit_count = int(row.get("hit_count") or 0)
                    success_count = int(row.get("success_count") or 0)

                    # 用 "能力名 + 归一化意图" 做 token 源，覆盖语义信息
                    text_for_token = f"{name} {case_norm}"
                    tokens_set = tokenize(text_for_token)

                    new_index[key] = {
                        "tokens_set": tokens_set,
                        "page_signature_hash": sig_hash,
                        "name": name,
                        "case_normalized": case_norm,
                        "hit_count": hit_count,
                        "success_count": success_count,
                    }
                except Exception as row_err:
                    _log.warning("FuzzyMatchEngine: 处理行异常 %s", row_err)
                    continue

            self._index = new_index
            self._built = True
            self._last_rebuild_at = time.time()
            self._last_rebuild_rows = len(new_index)
            self._hits_since_rebuild = 0

            _log.info("FuzzyMatchEngine: 索引重建完成，共 %d 条", len(new_index))
            return len(new_index)
        except Exception as e:
            _log.warning("FuzzyMatchEngine.rebuild_index 异常 %s", e)
            return 0
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as _pass_e:
                    _log.warning("__init__.py:163 异常被吞: %s", _pass_e, exc_info=True)

    def _ensure_index(self) -> bool:
        """懒加载：首次调用时才构建索引；后续每 100 次命中后重建一次。"""
        try:
            if not self._built:
                self.rebuild_index()
                return self._built
            if self._hits_since_rebuild >= self._REBUILD_EVERY_HITS:
                _log.info(
                    "FuzzyMatchEngine: 达到 %d 次命中，自动重建索引",
                    self._REBUILD_EVERY_HITS,
                )
                self.rebuild_index()
            return True
        except Exception as e:
            _log.warning("FuzzyMatchEngine._ensure_index 异常 %s", e)
            return False

    # ------------------------------------------------------------------
    # 查询 API
    # ------------------------------------------------------------------
    def fuzzy_match(
        self,
        current_intent_text: str,
        current_page_url: str = "",
        current_page_signature_hash: str = "",
        threshold: (
            float | None
        ) = None,  # ★ P1: 默认值,函数内走 _resolve_default_threshold()
        top_k: int = _DEFAULT_TOP_K,
    ) -> list[dict[str, Any]]:
        # ★ P1: 阈值支持 pipeline_config/env 覆盖
        if threshold is None:  # 用户没显式传,走配置
            threshold = self._resolve_default_threshold()
        """
        按当前意图模糊匹配缓存能力。

        返回:
            [{"key": str, "score": float, "name": str,
              "case_normalized": str, "page_signature_hash": str,
              "hit_count": int, "success_count": int}, ...]
        """
        try:
            # 1) 构建索引（懒加载）
            if not self._ensure_index():
                _record_fuzzy_metric(0, 0.0, 0.0, (current_intent_text or "")[:80])
                return []

            # 2) 对当前意图做 token 化
            current_tokens = tokenize(current_intent_text or "")
            if not current_tokens:
                _log.info("[FuzzyMatch] 当前意图无有效 token，跳过模糊匹配")
                _record_fuzzy_metric(0, 0.0, 0.0, "no_token")
                return []

            # 3) 遍历索引，计算 Jaccard 相似度
            scored: list[tuple[float, str, dict[str, Any]]] = []
            sig_input = (current_page_signature_hash or "").strip()
            sig_input_norm = sig_input.lower()

            for key, info in self._index.items():
                try:
                    j = jaccard(current_tokens, info["tokens_set"])
                    if j < threshold:
                        continue

                    # 若调用方提供了当前页面签名：做软匹配（一致则加成 0.08）
                    bonus = 0.0
                    if sig_input_norm and info.get("page_signature_hash"):
                        if info["page_signature_hash"].lower() == sig_input_norm:
                            bonus = 0.08
                        # 部分前缀匹配：给一个小加分（避免完全错过）
                        elif len(sig_input_norm) >= 8 and info[
                            "page_signature_hash"
                        ].lower().startswith(sig_input_norm[:8]):
                            bonus = 0.03

                    final = min(j + bonus, 1.0)
                    scored.append((final, key, info))
                except Exception as inner:
                    _log.warning("FuzzyMatchEngine: 计算 key=%s 时异常 %s", key, inner)
                    continue

            # 4) 按 score 降序，取 top-K
            scored.sort(key=lambda x: (-x[0], -x[2].get("success_count", 0)))
            top = scored[: max(1, int(top_k))]

            self._hits_since_rebuild += 1

            if not top:
                _record_fuzzy_metric(
                    0,
                    float(scored[0][0]) if scored else 0.0,
                    0.0,
                    (current_intent_text or "")[:80],
                )
                _log.info(
                    "[FuzzyMatch] 精确匹配失败，模糊匹配未找到超过阈值 %.2f 的候选",
                    threshold,
                )
                return []

            # 5) 组装返回结构 + 打印命中日志
            result: list[dict[str, Any]] = []
            for score, key, info in top:
                _log.info(
                    "[FuzzyMatch] 精确匹配失败，模糊命中 capability=%s score=%.2f",
                    info.get("name") or key,
                    score,
                )
                result.append(
                    {
                        "key": key,
                        "score": round(float(score), 4),
                        "name": info.get("name") or "",
                        "case_normalized": info.get("case_normalized") or "",
                        "page_signature_hash": info.get("page_signature_hash") or "",
                        "hit_count": int(info.get("hit_count") or 0),
                        "success_count": int(info.get("success_count") or 0),
                    }
                )
            # ★ KPI 埋点
            _record_fuzzy_metric(
                len(result),
                float(result[0]["score"]) if result else 0.0,
                0.0,
                (current_intent_text or "")[:80],
            )
            return result
        except Exception as e:
            _log.warning("FuzzyMatchEngine.fuzzy_match 顶层异常 %s", e)
            return []

    # ------------------------------------------------------------------
    # 管理 / 调试 API
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """返回引擎状态字典（用于调试 / 健康检查）。"""
        try:
            return {
                "built": self._built,
                "index_size": len(self._index),
                "last_rebuild_at": self._last_rebuild_at,
                "last_rebuild_rows": self._last_rebuild_rows,
                "hits_since_rebuild": self._hits_since_rebuild,
                "rebuild_every_hits": self._REBUILD_EVERY_HITS,
            }
        except Exception as e:
            _log.warning("FuzzyMatchEngine.status 异常 %s", e)
            return {"built": False, "error": str(e)}

    def is_available(self) -> bool:
        """判断引擎是否可用（至少成功构建过一次索引）。"""
        try:
            if self._built:
                return True
            # 尚未构建：尝试懒加载一次
            return self._ensure_index()
        except Exception as e:
            _log.warning("FuzzyMatchEngine.is_available 异常 %s", e)
            return False


# ---------------------------------------------------------------------------
# 便捷函数（调用方也可以直接 import 使用）
# ---------------------------------------------------------------------------
def fuzzy_match(
    current_intent_text: str,
    current_page_url: str = "",
    current_page_signature_hash: str = "",
    threshold: float = None,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    if threshold is None:
        threshold = FuzzyMatchEngine._resolve_default_threshold()
    return fuzzy_match_engine.fuzzy_match(
        current_intent_text=current_intent_text,
        current_page_url=current_page_url,
        current_page_signature_hash=current_page_signature_hash,
        threshold=threshold,
        top_k=top_k,
    )


def rebuild_index(force: bool = False) -> int:
    return fuzzy_match_engine.rebuild_index(force=force)


# 单例实例
# ★ 方案C 改造：fuzzy_match_engine 改为 PerProjectRegistry，按 project 隔离
from src.core.utils.per_project_registry import PerProjectRegistry

fuzzy_match_engine = PerProjectRegistry(FuzzyMatchEngine)
