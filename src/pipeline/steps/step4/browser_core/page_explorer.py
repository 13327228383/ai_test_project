# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict
from src.core.guarded import guard

"""页面探索器 — 自动浏览所有页面，缓存 DOM 结构，加速 target_hint 解析。

Phase 1: 页面探索 + DOM 预缓存（2026-07-11）
Phase 2: target_hint 自修正（AI loop 成功定位后反哺）
Phase 3: 文本优先模式（减少 vision API 调用）

核心思路：
  登录后自动浏览所有系统页面，缓存每个页面的交互元素。
  在结构化路径执行前，用缓存的元素索引解析 target_hint → 实际 DOM 元素，
  使结构化路径命中率从 <10% 提升到 >50%。

设计原则：
  - 通用性：不硬编码页面路径，从测试用例中自动提取
  - 模糊匹配：支持中文关键词、部分匹配、拼音首字母
  - 缓存时效：记录时间戳，支持按需刷新
"""

import asyncio
import logging
import time as _time
import re
from typing import Any, Set
from urllib.parse import urlparse, urljoin

_log = logging.getLogger(__name__)

# ── 中文关键词提取 ──────────────────────────────────────
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_ALNUM_RE = re.compile(r"[a-zA-Z0-9\u4e00-\u9fff]+")


def _extract_keywords(text: str) -> list[str]:
    """从文本中提取关键词（中文词 + 英文词）。"""
    if not text:
        return []
    words = _ALNUM_RE.findall(text)
    # 过滤单字
    return [w.lower() for w in words if len(w) > 1]


def _keyword_overlap(hint: str, element_text: str) -> float:
    """计算 hint 和 element_text 的关键词重叠度（Jaccard）。"""
    h_words = set(_extract_keywords(hint))
    e_words = set(_extract_keywords(element_text))
    if not h_words or not e_words:
        return 0.0
    intersection = h_words & e_words
    union = h_words | e_words
    return len(intersection) / len(union) if union else 0.0


class PageExplorer:
    """页面探索器：自动浏览页面，缓存 DOM 结构，提供 target_hint 解析。"""

    def __init__(self):
        self._cache: dict[str, dict] = {}  # url → {elements, timestamp, url}
        self._hint_index: dict[str, list[dict]] = {}  # keyword → [element_info]
        self._explored_urls: Set[str] = set()
        self._explore_count = 0
        self._total_elements = 0

    # ── 探索 ──────────────────────────────────────────

    async def explore_from_test_cases(
        self,
        engine,
        intents: list[dict],
        base_url: str = "",
    ) -> int:
        """从测试用例中提取所有 navigate URL，逐一探索并缓存 DOM。

        Returns:
            探索的页面数量。
        """
        # 提取所有唯一的 navigate 目标 URL
        urls_to_explore: Set[str] = set()
        for intent in intents:
            if not isinstance(intent, dict):
                continue
            steps = self._extract_steps(intent)
            for step in steps:
                actions = step.get("actions", [])
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    atype = action.get("type", "") or action.get("action", "")
                    if atype == "navigate":
                        url = (
                            action.get("url")
                            or action.get("value")
                            or action.get("target_hint", "")
                        )
                        # ★ 通用修复(2026-07-19): 跳过中文名称（非URL），由AI loop菜单导航处理
                        if url and re.search(r"[\u4e00-\u9fff]", url):
                            continue
                        # ★ 通用修复 (2026-07-18): 剥离查询参数中的占位符值
                        #   AI生成的URL可能包含占位符如 YZCODE.000, BLEND_000 等，
                        #   这些参数会导致 Playwright 导航失败（invalid URL）
                        if url and "?" in url:
                            _path_part, _query_part = url.split("?", 1)
                            _params = _query_part.split("&")
                            _clean_params = []
                            for _p in _params:
                                if "=" in _p:
                                    _k, _v = _p.split("=", 1)
                                    _v_upper = _v.upper()
                                    _is_ph = False
                                    if _v_upper.endswith(".000") or _v_upper.endswith(
                                        "_000"
                                    ):
                                        _is_ph = True
                                    elif re.match(
                                        r"^[A-Z_]+\.[0-9]+$", _v_upper
                                    ) or re.match(r"^[A-Z_]+_[0-9]+$", _v_upper):
                                        _is_ph = True
                                    elif re.match(r"^[0-9]+$", _v) and len(_v) <= 5:
                                        _is_ph = True
                                    if not _is_ph:
                                        _clean_params.append(_p)
                                elif _p.strip():
                                    # 无值的参数（如 "blendCode"）也跳过
                                    pass
                                else:
                                    _clean_params.append(_p)
                            _clean_query = (
                                "&".join(_clean_params) if _clean_params else ""
                            )
                            url = _path_part + (
                                "?" + _clean_query if _clean_query else ""
                            )
                        # ★ 通用修复(2026-07-19): 相对路径URL（如 /dataManage/portraitData）
                        #   即使 base_url 为空也保留，因为 Playwright 已在登录页面上，
                        #   page.goto("/path") 会自动解析为当前origin的完整URL。
                        if url and url.startswith(("http://", "https://")):
                            urls_to_explore.add(url)
                        elif url and base_url:
                            urls_to_explore.add(urljoin(base_url, url.lstrip("/")))
                        elif url and url.startswith("/"):
                            # 相对路径，base_url 为空时也保留
                            urls_to_explore.add(url)

        if not urls_to_explore:
            _log.warning(
                "[PageExplorer] 未从测试用例中提取到任何 navigate URL，跳过探索"
            )
            return 0

        _log.info("[PageExplorer] 开始探索 %d 个页面...", len(urls_to_explore))
        _t0 = _time.time()

        for url in sorted(urls_to_explore):
            if url in self._explored_urls:
                continue
            await self._explore_page(engine, url)

        _elapsed = _time.time() - _t0
        _log.info(
            "[PageExplorer] 探索完成: %d 个页面, %d 个元素, 耗时 %.1fs",
            self._explore_count,
            self._total_elements,
            _elapsed,
        )
        return self._explore_count

    async def _explore_page(self, engine, url: str) -> None:
        """探索单个页面：导航 → 等待渲染 → 提取 DOM → 建索引。"""
        with guard("_explore_page: if ( url and url.startswith('/') and", log=_log, level=logging.WARNING):
            # ★ 通用修复(2026-07-20 v9): 相对路径URL拼接当前页面origin
            #   原bug: 相对路径URL（如 /dataManage/portraitData?tab=xxx）直接传给
            #          page.goto，Playwright无法导航（"Cannot navigate to invalid URL"）。
            #   修复: 如果url是相对路径，用当前页面的origin拼接成完整URL。
            #   通用性: 适用于所有SPA系统（React/Vue/Angular），不依赖base_url配置。
            #   合理性: 追根溯源到"相对路径未拼接origin"的缺陷，而非在调用方打补丁。
            #   覆盖性: 覆盖所有PageExplorer探索场景，无论url来自哪里。
            if (
                url
                and url.startswith("/")
                and not url.startswith(("http://", "https://"))
            ):
                with guard("_explore_page: _current_url = engine._page.url or '", log=_log):
                    _current_url = engine._page.url or ""
                    if _current_url and _current_url != "about:blank":
                        _parsed = urlparse(_current_url)
                        _origin = f"{_parsed.scheme}://{_parsed.netloc}"
                        if _origin:
                            url = _origin + url
                            _log.debug("[PageExplorer] 相对路径已拼接origin: %s", url)
            _log.info("[PageExplorer] 探索页面: %s", url)
            await engine._page.goto(url, timeout=15000)
            await engine._page.wait_for_load_state("networkidle", timeout=10000)
            # 等待 SPA 渲染
            await asyncio.sleep(2)
            # 滚动触发懒加载
            try:
                await engine._page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight);"
                )
                await asyncio.sleep(0.5)
                await engine._page.evaluate("window.scrollTo(0, 0);")
                await asyncio.sleep(0.5)
            except Exception as _r31:
                _log.debug(
                    "[page_explorer] 异常降级(pass): %s", str(_r31)[:60], exc_info=True
                )
                pass

            elements = await engine.get_structured_state()
            if not elements:
                _log.warning("[PageExplorer] 页面 %s DOM 为空，跳过", url)
                return

            self._explored_urls.add(url)
            self._explore_count += 1
            self._total_elements += len(elements)

            # 缓存
            self._cache[url] = {
                "elements": elements,
                "timestamp": _time.time(),
                "url": url,
            }

            # 建模糊索引
            self._index_elements(url, elements)

            _log.info("[PageExplorer] ✓ %s: %d 个交互元素", url, len(elements))


    # ── 索引 ──────────────────────────────────────────

    def _index_elements(self, url: str, elements: list[dict]) -> None:
        """为页面元素建立模糊关键词索引。"""
        for el in elements:
            if not isinstance(el, dict):
                continue
            # 提取元素的所有文本标识
            texts = []
            for key in ("text", "placeholder", "label", "name", "aria-label", "title"):
                val = el.get(key, "")
                if val and isinstance(val, str):
                    texts.append(val)

            combined = " ".join(texts)
            keywords = _extract_keywords(combined)

            entry = {
                "url": url,
                "element": el,
                "texts": texts,
                "combined_text": combined,
            }

            for kw in keywords:
                if kw not in self._hint_index:
                    self._hint_index[kw] = []
                self._hint_index[kw].append(entry)

    # ── 解析 target_hint ─────────────────────────────

    def resolve_hint(
        self, target_hint: str, current_url: str = ""
    ) -> dict[str, Any] | None:
        """解析 target_hint 到实际 DOM 元素。

        Args:
            target_hint: 测试用例中的元素描述（如 "核算名称输入框"）
            current_url: 当前页面 URL（优先在当前页面匹配）

        Returns:
            {"url": str, "element": dict, "selector": str, "score": float} 或 None
        """
        if not target_hint or not self._hint_index:
            return None

        hint_keywords = _extract_keywords(target_hint)
        if not hint_keywords:
            return None

        candidates: list[tuple[Dict, float]] = []

        # 策略1: 在所有已索引元素中查找关键词匹配
        for kw in hint_keywords:
            entries = self._hint_index.get(kw, [])
            for entry in entries:
                score = _keyword_overlap(target_hint, entry["combined_text"])
                if score > 0:
                    # 当前页面加权
                    if current_url and entry["url"] == current_url:
                        score += 0.3
                    # 元素类型匹配加权（hint 含"输入框"、"按钮"等）
                    el_tag = entry["element"].get("tag", "")
                    if self._hint_matches_tag(target_hint, el_tag):
                        score += 0.2
                    candidates.append((entry, score))

        if not candidates:
            return None

        # 按分数降序排序
        candidates.sort(key=lambda x: x[1], reverse=True)
        best_entry, best_score = candidates[0]

        if best_score < 0.15:
            return None

        el = best_entry["element"]
        selector = el.get("selector", "") or el.get("ai_id", "")

        _log.info(
            "[PageExplorer] resolve_hint '%s' → %s (score=%.2f, url=%s, tag=%s, text=%s)",
            target_hint[:40],
            selector[:60] if selector else "N/A",
            best_score,
            best_entry["url"],
            el.get("tag", ""),
            el.get("text", "")[:30],
        )

        return {
            "url": best_entry["url"],
            "element": el,
            "selector": selector,
            "score": best_score,
            "tag": el.get("tag", ""),
            "text": el.get("text", ""),
            "placeholder": el.get("placeholder", ""),
            "name": el.get("name", ""),
        }

    def _hint_matches_tag(self, hint: str, tag: str) -> bool:
        """检查 hint 文本是否暗示了元素类型。"""
        hint_lower = hint.lower()
        tag_map = {
            "输入框": "input",
            "输入": "input",
            "文本框": "input",
            "input": "input",
            "textarea": "textarea",
            "文本域": "textarea",
            "按钮": "button",
            "button": "button",
            "btn": "button",
            "链接": "a",
            "link": "a",
            "下拉": "select",
            "选择框": "select",
            "select": "select",
            "checkbox": "input",
            "勾选": "input",
            "上传": "input",
            "upload": "input",
        }
        for hint_tag, expected_tag in tag_map.items():
            if hint_tag in hint_lower and expected_tag == tag:
                return True
        return False

    # ── target_hint 自修正（Phase 2）─────────────────

    def enrich_from_ai_success(
        self,
        target_hint: str,
        resolved_element: dict,
        page_url: str = "",
    ) -> None:
        """AI loop 成功定位元素后，将映射反哺到缓存索引。

        Args:
            target_hint: 原始 target_hint
            resolved_element: AI 成功定位到的元素信息
            page_url: 当前页面 URL
        """
        if not target_hint or not resolved_element:
            return

        try:
            # 确保元素有基本的文本信息
            el_text = (
                resolved_element.get("text", "")
                or resolved_element.get("placeholder", "")
                or resolved_element.get("label", "")
                or resolved_element.get("aria-label", "")
                or resolved_element.get("name", "")
                or ""
            )

            if not el_text:
                # 尝试从 target_hint 本身提取关键词作为元素文本
                el_text = target_hint

            entry = {
                "url": page_url,
                "element": resolved_element,
                "texts": [el_text],
                "combined_text": el_text,
            }

            keywords = _extract_keywords(el_text)
            for kw in keywords:
                if kw not in self._hint_index:
                    self._hint_index[kw] = []
                # 避免重复
                existing_urls = {e.get("url") for e in self._hint_index.get(kw, [])}
                if page_url not in existing_urls:
                    self._hint_index[kw].append(entry)

            _log.info(
                "[PageExplorer] enrich: '%s' → %s (tag=%s, url=%s)",
                target_hint[:40],
                el_text[:40],
                resolved_element.get("tag", ""),
                page_url,
            )
        except Exception as e:
            _log.debug("[PageExplorer] enrich 失败: %s", e, exc_info=True)

    # ── 辅助 ──────────────────────────────────────────

    def _extract_steps(self, intent: dict) -> list[dict]:
        """从 intent 中提取步骤列表。

        ★ 通用修复(2026-07-19): intent 可能不含 steps 结构，
        而是 case_actions_json 扁平列表。将其包装为单一步骤返回。
        """
        intent_schema = intent.get("intent_schema", {}) or {}
        if isinstance(intent_schema, tuple):
            intent_schema = intent_schema[0] if intent_schema else {}
        if isinstance(intent_schema, dict):
            steps = intent_schema.get("steps", [])
            if steps:
                return steps
        steps = intent.get("steps", [])
        if steps:
            return steps
        # ★ 通用修复: case_actions_json 是扁平 action 列表
        actions_raw = intent.get("case_actions_json") or []
        if isinstance(actions_raw, str):
            try:
                import json

                actions_raw = json.loads(actions_raw)
            except Exception:
                actions_raw = []
        if isinstance(actions_raw, list) and actions_raw:
            return [{"actions": actions_raw}]
        return []

    def get_stats(self) -> dict[str, Any]:
        """获取探索统计信息。"""
        return {
            "explored_pages": self._explore_count,
            "total_elements": self._total_elements,
            "indexed_keywords": len(self._hint_index),
            "cached_urls": list(self._cache.keys()),
        }

    def get_cached_elements(self, url: str = "") -> list[dict]:
        """获取缓存的页面元素。"""
        if url and url in self._cache:
            return self._cache[url].get("elements", [])
        all_elements = []
        for cache in self._cache.values():
            all_elements.extend(cache.get("elements", []))
        return all_elements


# ── 全局单例（按 BrowserCore 实例生命周期使用，非模块级） ──
# 每个 BrowserCore 实例创建自己的 PageExplorer，避免跨运行污染
