# -*- coding: utf-8 -*-
"""★ T04.1: 本地"见图转换"视觉校验后端 LocalBackend [已复核-DeepSeek-V3 2026-08-16]
纯计算、零外部依赖（PIL 可选）；无充分证据返回 NO_DATA 而非假 PASS。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from src.oracle.vlm.protocol import VLMAdapter, VisualStatus, VisualVerdict

_log = logging.getLogger(__name__)

_DOM_SIM_PASS = 0.95   # DOM 结构相似度 ≥ 此值 → PASS 信号
_DOM_SIM_FAIL = 0.30   # ≤ 此值 → FAIL 信号
_PHASH_DIST_PASS = 10  # 感知哈希海明距离 ≤ 此值 → PASS 信号(64bit)
_PHASH_DIST_FAIL = 25  # ≥ 此值 → FAIL 信号


class DomDiffer:
    """DOM 结构对比：token 集合 Jaccard 相似度（无外部依赖）。"""
    _TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]+")

    @classmethod
    def _tokens(cls, text: str) -> set:
        return set(cls._TOKEN_RE.findall(str(text))) if text else set()

    @classmethod
    def structural_similarity(cls, a: str, b: str) -> float:
        ta, tb = cls._tokens(a), cls._tokens(b)
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    @classmethod
    def diff(cls, baseline_dom: str, current_dom: str) -> dict:
        ta, tb = cls._tokens(baseline_dom), cls._tokens(current_dom)
        return {
            "added": sorted(tb - ta)[:20],
            "removed": sorted(ta - tb)[:20],
            "similarity": cls.structural_similarity(baseline_dom, current_dom),
        }


class PerceptualHasher:
    """截图感知哈希（aHash：8x8 灰度均值 → 64bit）；PIL 缺失返回 None 降级。"""
    _HASH_SIZE = 8

    @staticmethod
    def is_pillow_available() -> bool:
        try:
            import PIL.Image  # noqa: F401

            return True
        except Exception:
            return False

    @classmethod
    def phash(cls, image_bytes: bytes) -> Optional[str]:
        if not image_bytes or not cls.is_pillow_available():
            return None
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as img:
                gray = img.convert("L").resize(
                    (cls._HASH_SIZE, cls._HASH_SIZE), Image.LANCZOS
                )
                pixels = list(gray.getdata())
            mean = sum(pixels) / len(pixels)
            return "".join("1" if p >= mean else "0" for p in pixels)
        except Exception as e:
            _log.debug("[PerceptualHasher] phash 失败(降级): %s", str(e)[:80])
            return None

    @staticmethod
    def distance(hash_a: str, hash_b: str) -> int:
        n = min(len(hash_a), len(hash_b))
        if n == 0:
            return 64
        return sum(1 for i in range(n) if hash_a[i] != hash_b[i])


class ElementAsserter:
    """期望元素在 DOM 文本中的存在性断言（引号/书名号关键词/子串匹配）。"""
    _QUOTE_RE = re.compile(r"[\"'\u201c\u2018\u300c]([^\"'\u201d\u2019\u300d]{1,30})[\"'\u201d\u2019\u300d]")

    @classmethod
    def extract_keywords(cls, assertion: str) -> list:
        """从断言提取候选元素关键词（优先引号内文本）。"""
        if not assertion:
            return []
        return [q.strip() for q in cls._QUOTE_RE.findall(str(assertion)) if q.strip()]

    @classmethod
    def assert_exists(cls, dom_text: str, keyword: str) -> Optional[bool]:
        """关键字是否出现在 DOM 文本中；DOM 为空无法断言返回 None。"""
        if not dom_text or not keyword:
            return None
        return str(keyword).lower() in str(dom_text).lower()


class LocalBackend(VLMAdapter):
    """本地规则后端：DOM 结构 + 感知哈希 + 元素存在性三路组合，fail-closed。"""

    name: str = "local_dom_phash"
    priority: int = 10
    confidence_cap: float = 0.7

    def __init__(self) -> None:
        self.differ = DomDiffer()
        self.hasher = PerceptualHasher()
        self.asserter = ElementAsserter()

    def is_available(self) -> bool:
        return True

    def _no_data(self, reason: str, detail: dict | None = None) -> VisualVerdict:
        evidence = {"backend": self.name, "reason": reason}
        if detail:
            evidence.update(detail)
        return VisualVerdict(
            status=VisualStatus.NO_DATA,
            confidence=0.0,
            evidence=evidence,
            backend_name=self.name,
        )

    def verify(
        self,
        screenshot_bytes: Optional[bytes],
        assertion: str,
        context: dict[str, Any],
    ) -> VisualVerdict:
        """组合三组件；context: dom_summary / baseline_dom / baseline_screenshot / expected_elements"""
        dom_text = str(context.get("dom_summary") or "").strip()
        baseline_dom = str(context.get("baseline_dom") or "").strip()
        baseline_shot = context.get("baseline_screenshot")
        expected = list(context.get("expected_elements") or [])

        signals: list[tuple[str, float, str]] = []
        # ★ 去重关键词：assertion 提取与 expected_elements 可能重复，先去重再判单数，
        #   否则「单一期望关键词缺失→FAIL」因重复词合计 len≥2 而堕落为 NO_DATA。
        keywords: list[str] = []
        _seen: set[str] = set()
        for kw in (self.asserter.extract_keywords(assertion) + list(expected)):
            if kw and kw not in _seen:
                _seen.add(kw)
                keywords.append(kw)
        if keywords and dom_text:
            for kw in keywords[:3]:
                found = self.asserter.assert_exists(dom_text, kw)
                if found is True:
                    signals.append(("PASS", 0.55, f"期望元素已找到: {kw[:20]}"))
                elif found is False and len(keywords) == 1:
                    signals.append(("FAIL", 0.45, f"期望元素未在 DOM 出现: {kw[:20]}"))

        if baseline_dom and dom_text:
            sim = self.differ.structural_similarity(baseline_dom, dom_text)
            if sim >= _DOM_SIM_PASS:
                signals.append(("PASS", 0.6, f"DOM 结构与基线一致(sim={sim:.2f})"))
            elif sim <= _DOM_SIM_FAIL:
                signals.append(("FAIL", 0.6, f"DOM 结构与基线差异过大(sim={sim:.2f})"))

        if baseline_shot and screenshot_bytes and self.hasher.is_pillow_available():
            h1 = self.hasher.phash(baseline_shot)
            h2 = self.hasher.phash(screenshot_bytes)
            if h1 and h2:
                dist = self.hasher.distance(h1, h2)
                if dist <= _PHASH_DIST_PASS:
                    signals.append(("PASS", 0.6, f"截图与基线一致(hash_dist={dist})"))
                elif dist >= _PHASH_DIST_FAIL:
                    signals.append(("FAIL", 0.6, f"截图与基线差异明显(hash_dist={dist})"))

        detail = {
            "dom_similarity": (
                round(self.differ.structural_similarity(baseline_dom, dom_text), 3)
                if baseline_dom and dom_text
                else None
            )
        }
        if signals:
            fail_signals = [s for s in signals if s[0] == "FAIL"]
            pass_signals = [s for s in signals if s[0] == "PASS"]
            # FAIL 优先：宁可不报通过（fail-closed）
            chosen = fail_signals or pass_signals
            conf = max(s[1] for s in chosen)
            reason = "; ".join(s[2] for s in chosen)
            status = VisualStatus.FAIL if fail_signals else VisualStatus.PASS
            return VisualVerdict(
                status=status,
                confidence=min(conf, self.confidence_cap),
                evidence={"backend": self.name, "reason": reason, **detail},
                backend_name=self.name,
            )
        return self._no_data(
            "本地视觉校验无足够证据（缺 baseline/期望元素/DOM）", detail=detail
        )
