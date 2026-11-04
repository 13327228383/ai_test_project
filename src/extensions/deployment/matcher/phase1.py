from typing import Dict
# -*- coding: utf-8 -*-
"""
Matcher Phase1 Mixin: 模块名模糊匹配
"""

import re


class _MatcherPhase1Mixin:
    """提供 Phase1 模块匹配相关方法。"""

    # ═══════════════════════════════════════════
    # Phase 1: 模块名模糊匹配
    # ═══════════════════════════════════════════

    @staticmethod
    def _match_module(deployment_module: str, intent_catalog_name: str) -> float:
        """
        计算部署文档模块名与测试用例目录名的相似度。

        算法：
        - 精确匹配 → 1.0
        - 单向/双向包含 → 0.8
        - 字符 bigram Jaccard → 0.0~1.0
        """
        if not deployment_module or not intent_catalog_name:
            return 0.0

        dm = str(deployment_module).strip()
        cc = str(intent_catalog_name).strip()

        if dm == cc:
            return 1.0
        if dm in cc or cc in dm:
            return 0.8

        # 去掉分隔符再比一次
        dm_clean = dm.replace("-", "").replace(" ", "").replace("、", "")
        cc_clean = cc.replace("-", "").replace(" ", "").replace("、", "")
        if dm_clean == cc_clean:
            return 1.0
        if dm_clean in cc_clean or cc_clean in dm_clean:
            return 0.8

        # 字符 bigram Jaccard
        dm_grams = set(dm[i : i + 2] for i in range(len(dm) - 1))
        cc_grams = set(cc[i : i + 2] for i in range(len(cc) - 1))
        if not dm_grams or not cc_grams:
            return 0.0
        intersection = dm_grams & cc_grams
        union = dm_grams | cc_grams
        return len(intersection) / len(union)

    @staticmethod
    def _search_module_in_intent(module: str, intent: dict) -> float:
        """
        在测试用例的 title + steps 中搜索模块关键词。
        用于没有 catalog_name 的测试用例集。
        """
        if not module:
            return 0.5  # 无模块信息，不设障碍

        # 构建测试用例文本
        title = str(intent.get("title") or "")
        steps = intent.get("case_actions_json") or []
        if isinstance(steps, list):
            steps_text = " ".join(
                s.get("description", str(s)) if isinstance(s, dict) else str(s)
                for s in steps
            )
        else:
            steps_text = str(steps)
        intent_text = f"{title} {steps_text}"

        module_clean = module.strip()
        intent_clean = re.sub(r'[，。！？、；："\s]+', "", intent_text)

        # 1. 完整匹配
        if module_clean in intent_clean:
            return 1.0

        # 2. 拆分模块名（按 - / > 等分隔符）
        parts = re.split(r"[-/>\s]+", module_clean)
        parts = [p for p in parts if len(p) >= 2]

        if not parts:
            return 0.5

        # 3. 检查每个 part 是否出现在 intent 中
        matched = sum(1 for p in parts if p in intent_clean)
        ratio = matched / len(parts)

        if ratio >= 0.5:
            return 0.3 + 0.5 * ratio  # 0.55~0.8
        elif ratio > 0:
            return 0.1 + 0.4 * ratio  # 0.14~0.26
        else:
            # 4. 最后的 fallback
            return _MatcherPhase1Mixin._match_module(module_clean, title) * 0.3

    @staticmethod
    def _fallback_search(feature: str, intents: list[dict]) -> list[tuple[float, Dict]]:
        """
        Phase 1 无候选时的 fallback：用 feature 核心词在测试用例中搜索。
        """
        if not feature:
            return [(0.3, intent) for intent in intents]  # 完全放行

        ft = re.sub(r"[^\w一-鿿]", "", feature)

        # 提取核心词（2-3 字中文）
        core_words = set()
        for n in [2, 3]:
            for i in range(len(ft) - n + 1):
                chunk = ft[i : i + n]
                if re.search(r"[一-鿿]", chunk):
                    core_words.add(chunk)

        if not core_words:
            return [(0.3, intent) for intent in intents]

        results = []
        for intent in intents:
            title = str(intent.get("title") or "")
            steps = intent.get("case_actions_json") or []
            if isinstance(steps, list):
                steps_text = " ".join(
                    s.get("description", str(s)) if isinstance(s, dict) else str(s)
                    for s in steps
                )
            else:
                steps_text = str(steps)
            intent_text = f"{title} {steps_text}"
            intent_clean = re.sub(r"[^\w一-鿿]", "", intent_text)

            matched = sum(1 for kw in core_words if kw in intent_clean)
            ratio = matched / len(core_words) if core_words else 0.0

            if ratio >= 0.1:
                score = 0.1 + 0.5 * ratio
                results.append((score, intent))

        return results

    @staticmethod
    def _extract_module_hint(intent: dict) -> str:
        """
        当 catalog_name 为空时，从 intent 的 title + steps 中提取模块提示。
        """
        hints = []

        # 从 steps 中提取导航路径
        steps = intent.get("case_actions_json") or []
        if isinstance(steps, list):
            for step in steps:
                text = (
                    step.get("description", str(step))
                    if isinstance(step, dict)
                    else str(step)
                )
                nav_match = re.findall(r"[一-鿿\w]+(?:\s*>\s*[一-鿿\w]+)+", text)
                for nav in nav_match:
                    parts = [p.strip() for p in nav.split(">")]
                    hints.extend(parts)
                target_match = re.findall(
                    r"(?:进入|打开|点击)([一-鿿\w]{2,8})(?:页面|模块|菜单|标签)?", text
                )
                hints.extend(target_match)

        # 从 title 中提取关键名词
        title = str(intent.get("title") or "")
        title_match = re.findall(
            r"验证([一-鿿\w]{2,10})(?:的|中|在|功能|模块|页面)", title
        )
        hints.extend(title_match)

        return " ".join(hints) if hints else title
