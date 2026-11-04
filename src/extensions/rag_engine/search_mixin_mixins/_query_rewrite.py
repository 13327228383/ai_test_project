# -*- coding: utf-8 -*-
from __future__ import annotations

"""Query 改写 / 同义词扩展 mixin。"""

import re
import logging
import threading
from typing import Any

_log = logging.getLogger("rag_engine")


class SearchQueryRewriteMixin:
    """Query 改写、同义词扩展、LLM 改写相关方法。"""

    def _load_synonym_dict(self) -> dict[str, list[str]]:
        """尝试从 synonym_routes 加载同义词字典；失败返回空字典。"""
        try:
            from src.backend.routes.synonym_routes import _load_synonyms  # type: ignore

            data = _load_synonyms()
            if isinstance(data, dict):
                return {str(k): list(v) for k, v in data.items() if v}
        except Exception as _e:
            _log.debug("write + load + _load_synonym_dict: %s", _e, exc_info=True)
        # ── 兜底：直接从 synonym_library 表加载（若可用） ──
        try:
            from src.models.base import get_conn  # type: ignore

            c = get_conn()
            rows = (
                c.execute(
                    "SELECT core_word, synonym_group FROM synonym_library"
                ).fetchall()
                or []
            )
            try:
                c.close()
            except Exception as _e:
                _log.debug("import + model + execute: %s", _e, exc_info=True)
            result: dict[str, list[str]] = {}
            for r in rows:
                core = (r["core_word"] if isinstance(r, dict) else r[0]) or ""
                grp = (r["synonym_group"] if isinstance(r, dict) else r[1]) or ""
                core = str(core).strip()
                if not core:
                    continue
                parts = [
                    x for s in re.split(r"[,，;；\s]+", str(grp)) if (x := s.strip())
                ]
                if parts:
                    result[core] = parts
            return result
        except Exception as _e:
            _log.debug("同义词字典兜底加载失败: %s", _e, exc_info=True)
            return {}

    def _expand_with_synonyms(
        self, query: str, syn_dict: dict[str, list[str]]
    ) -> str | None:
        """对 query 中出现的核心词，把同义词列表附加到末尾形成扩展 query。"""
        if not query or not syn_dict:
            return None
        hits: list[str] = []
        for core, group in syn_dict.items():
            if not core:
                continue
            if core in query:
                for s in group:
                    if s and s != core and s not in query:
                        hits.append(s)
        if not hits:
            return None
        # 去重保序
        seen, ordered = set(), []
        for s in hits:
            if s not in seen:
                seen.add(s)
                ordered.append(s)
        return query + " " + " ".join(ordered[:8])

    def _llm_rewrite_query(self, query: str, timeout: float = 5.0) -> list[str]:
        """用 AI 改写 query 成 1-2 个等价但表述不同的子 query；失败安静返回 []。"""
        if not query:
            return []
        try:
            # ★ 任务规约要求 from src.ai.ai_client import AiClient；本仓使用 AiBrain 单例
            try:
                from src.ai.ai_client import AiClient  # type: ignore

                ai = AiClient()  # type: ignore
            except Exception as _ae:
                _log.debug(
                    "[query_rewrite] AiClient初始化失败(用ai_brain): %s", str(_ae)[:50]
                )
                from src.ai.ai_client import ai_brain as ai  # type: ignore
        except Exception as _e:
            _log.debug("AI 客户端初始化失败: %s", _e, exc_info=True)
            return []

        sys_prompt = (
            "你是一个搜索 query 改写助手。给定一个查询，请输出 1-2 条与之语义等价但用词/角度不同的"
            "中文改写句，每行一条，不要编号、不要解释、不要标点开头。"
        )
        try:
            container: dict[str, Any] = {}

            def _worker():
                try:
                    # ★ 传 task_type 走 router（统一入口，尊重 task_provider 配置）
                    #   AiBrain 无 chat 方法，移除死代码分支
                    container["res"] = ai._call_api(
                        system_prompt=sys_prompt,
                        user_message=str(query),
                        temperature=0.3,
                        max_tokens=256,
                        caller_name="rag_query_rewrite",
                        task_type="general",
                    )
                except Exception as e:
                    container["err"] = e

            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            t.join(timeout=max(1.0, float(timeout)))
            if t.is_alive():
                return []
            res = container.get("res") or {}
            if not (isinstance(res, dict) and res.get("success")):
                return []
            text = (res.get("data") or "").strip()
            lines = [ln.strip(" -•·\t") for ln in text.splitlines() if ln.strip()]
            # 过滤太短/与原句相同
            out: list[str] = []
            for ln in lines:
                if 3 <= len(ln) <= 200 and ln != query:
                    out.append(ln)
                if len(out) >= 2:
                    break
            return out
        except Exception as _e:
            _log.debug("LLM 改写查询失败: %s", _e, exc_info=True)
            return []

    def _expand_query(self, query: str) -> list[str]:
        """返回 [原 query, 同义词扩展, LLM 改写...]，至少 1 条；失败安静兜底为 [原 query]。

        仅在 pipeline_config.rag_enable_query_rewrite=True 时启用扩展，否则只返回原 query。
        """
        original = (query or "").strip()
        if not original:
            return []
        try:
            from src.core.pipeline_config import pipeline_config

            enabled = bool(getattr(pipeline_config, "rag_enable_query_rewrite", False))
        except Exception as _ce:
            _log.debug("[query_rewrite] 配置读取失败(用False): %s", str(_ce)[:50])
            enabled = False
        if not enabled:
            return [original]

        results: list[str] = [original]
        # 1) 同义词扩展
        try:
            syn_dict = self._load_synonym_dict()
            expanded = self._expand_with_synonyms(original, syn_dict)
            if expanded and expanded != original:
                results.append(expanded)
        except Exception as _e:
            _log.debug("load: %s", _e, exc_info=True)
        # 2) LLM 改写（带超时）
        try:
            timeout = float(getattr(pipeline_config, "rag_query_rewrite_timeout", 5.0))
        except Exception as _ce:
            _log.debug("[query_rewrite] timeout配置失败(用5.0): %s", str(_ce)[:50])
            timeout = 5.0
        try:
            for q in self._llm_rewrite_query(original, timeout=timeout):
                if q and q not in results:
                    results.append(q)
        except Exception as _e:
            _log.debug("write + rag + write: %s", _e, exc_info=True)
        return results[:4]
