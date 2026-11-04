# -*- coding: utf-8 -*-
from __future__ import annotations

"""
_detect.py — 主判定 API + 解释
===============================
作为 _BusinessCapDetectMixin 提供给 BusinessCapabilities 多继承体系。
包含 detect_capability（主判定）和 explain_last（最近一次结果解释）。
"""

import logging
from typing import Any

_log = logging.getLogger(__name__)


class _BusinessCapDetectMixin:
    """判定 mixin：detect_capability（主判定）+ explain_last（解释）。"""

    # ------------------------------------------------------------------
    # 主判定 API
    # ------------------------------------------------------------------
    def detect_capability(
        self,
        compact_state: Any,
        goal_text: str,
        intent_schema: dict[str, Any] | None = None,
    ) -> tuple[str, float] | None:
        """
        根据 compact_state（DOM 元素列表） + goal_text 判断最匹配的业务能力。

        返回: (capability_hint, score) 或 None。
        """
        try:
            # 1) 兼容不同传入形式：list / dict 都要能使用
            elements: list[Any] = []
            if isinstance(compact_state, list):
                elements = compact_state
            elif isinstance(compact_state, dict):
                elements = (
                    compact_state.get("elements") or compact_state.get("dom") or []
                )
                if not isinstance(elements, list):
                    elements = [elements]

            # 2) intent_schema 的 type 字段即为能力分类（v9）
            schema = intent_schema or {}
            explicit = (schema.get("type") or "").strip()
            if explicit and explicit in self._registry:
                _log.info("detect_capability: 意图显式声明 capability=%s", explicit)
                self._last_result = (explicit, 1.0)
                # 显式声明即埋点
                self._record_capability_metric(
                    explicit, "explicit", 1.0, goal_text or ""
                )
                return self._last_result

            goal_lower = (goal_text or "").lower()

            # 3a) 纯文本模式：DOM 元素不足（< 3 个）时，只靠关键词匹配
            #     —— 用于意图推断阶段、Smoke test、无浏览器场景
            if len(elements) < 3:
                best_text: tuple[str, float] | None = None
                for hint, info in self._registry.items():
                    try:
                        kws = [k.lower() for k in info.get("keywords", [])]
                        if not kws:
                            continue
                        matched = sum(1 for kw in kws if kw and kw in goal_lower)
                        if matched <= 0:
                            continue
                        # 评分: 基础分 (匹配到即有分) + 关键词覆盖率 + 命中数加成
                        # 1个关键词 ≈ 0.42，2个关键词 ≈ 0.55，3个关键词 ≈ 0.65
                        # 评分: 1个关键词=0.43, 2个=0.51, 3个=0.59
                        # 阈值 0.50 → 至少2个关键词命中才激活能力路径，防止单关键词弱匹配
                        text_score = min(0.9, 0.35 + min(matched, 5) * 0.08)
                        if text_score >= 0.50 and (
                            best_text is None or text_score > best_text[1]
                        ):
                            best_text = (hint, text_score)
                    except Exception as _in:
                        _log.debug("纯文本模式 detector[%s] 异常 %s", hint, _in)
                        continue
                if best_text:
                    self._last_result = best_text
                    # 纯文本模式命中
                    self._record_capability_metric(
                        best_text[0], "text", best_text[1], goal_text or ""
                    )
                    _log.info(
                        "detect_capability: 纯文本模式命中 hint=%s score=%.2f (DOM=%d 元素)",
                        best_text[0],
                        best_text[1],
                        len(elements),
                    )
                    return best_text
                _log.info(
                    "detect_capability: 纯文本模式未命中 goal=%s DOM=%d",
                    goal_text[:60],
                    len(elements),
                )
                self._record_capability_miss("纯文本模式未匹配到关键词")
                return None

            # 3b) DOM 证据模式：元素足够时，跑每个能力的 detector 做精细判断
            best: tuple[str, float] | None = None
            for hint, info in self._registry.items():
                try:
                    if len(elements) < info.get("min_elements", 0):
                        continue
                    detector = info.get("detector")
                    if not callable(detector):
                        continue
                    score = float(detector(elements, goal_text or "") or 0.0)
                    score = max(0.0, min(score, 1.0))
                    if score >= 0.5 and (best is None or score > best[1]):
                        best = (hint, score)
                except Exception as inner:
                    _log.warning("detector[%s] 异常 %s", hint, inner)
                    continue

            if best is None:
                self._last_result = None
                self._record_capability_miss("DOM证据模式未命中")
                _log.info(
                    "detect_capability: DOM 证据模式未命中 goal=%s", goal_text[:60]
                )
                return None

            self._last_result = best
            self._record_capability_metric(best[0], "dom", best[1], goal_text or "")
            _log.info(
                "detect_capability: DOM证据模式命中 hint=%s score=%.2f",
                best[0],
                best[1],
            )
            return best
        except Exception as e:
            _log.warning("detect_capability 顶层异常 %s", e)
            return None

    def explain_last(self) -> dict[str, Any]:
        """返回最近一次判定结果的可读解释（用于日志/调试）。"""
        try:
            if self._last_result is None:
                return {"hint": None, "score": 0.0, "message": "未匹配到任何能力"}
            hint, score = self._last_result
            info = self.get_capability(hint) or {}
            return {
                "hint": hint,
                "score": score,
                "display_name": info.get("display_name", hint),
                "description": info.get("description", ""),
            }
        except Exception as e:
            _log.warning("explain_last 异常 %s", e)
            return {"hint": None, "score": 0.0, "message": str(e)}
