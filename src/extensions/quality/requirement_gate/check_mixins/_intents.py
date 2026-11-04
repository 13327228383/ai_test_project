# -*- coding: utf-8 -*-
"""
门禁2：测试用例生成后门禁 Mixin
包含：check_intents
"""

import logging
from src.core.guarded import guard
from typing import Any

_log = logging.getLogger(__name__)

from src.core.pipeline_config import pipeline_config
from ..constants import _GateConstantsMixin, GateResult


class _GateIntentsMixin(_GateConstantsMixin):
    """测试用例门禁 Mixin —— 校验测试用例及其步骤是否有需求依据。"""

    # ══════════════════════════════════════════════════════════
    # 门禁2：测试用例生成后门禁
    # ══════════════════════════════════════════════════════════

    def check_intents(self, intents: list[dict[str, Any]]) -> GateResult:
        """
        校验测试用例是否在需求文档中有依据。

        Args:
            intents: 测试用例列表

        Returns:
            GateResult: 阻断项 = 需求文档中找不到依据的测试用例/步骤
        """
        result = GateResult()

        if not self.requirement_text:
            result.passed = False
            result.blockers.append(
                {
                    "case_id": "SYSTEM",
                    "reason": "【阻断】合并需求文档为空，无法校验测试用例。请先执行Step1加载需求。",
                    "type": "baseline_missing",
                }
            )
            result.summary = "基准缺失，无法校验"
            return result

        for intent in intents:
            case_id = intent.get("case_id", "?")
            title = intent.get("title", "")
            desc = intent.get("description", "")
            steps = intent.get("steps", [])

            if intent.get("_generation_source") in _EXEMPT_SOURCES:
                _log.error(
                    "[Gate] 豁免用例 '%s' (source=%s)",
                    title[:50],
                    intent.get("_generation_source"),
                )
                continue
            # ★ §12.2规则3 降级用例豁免(2026-07-26): 降级用例已通过 DOM 真实性校验，天然有依据
            #   根因(§10.9 5层):
            #     L1: TC-REQ-002-D01/004-D01 降级用例被门禁阻断
            #     L2: _generation_source='page_merged' 不在豁免列表
            #     L3: page_merged 合并(_modeling_intents.py:739)覆盖原 _generation_source
            #     L4: 降级用例 low_confidence=True 标记未被门禁识别
            #     L5: 门禁豁免逻辑只看 _generation_source，不看 low_confidence
            #   通用性(§12.4): low_confidence 是 _build_downgrade_cases 统一设置的降级标记，
            #                  跨系统/跨行业/跨页面通用，无业务依赖
            #   合理性: 降级用例已通过 §12.2规则3 DOM 真实性校验（target_hint 在 DOM 中存在），
            #           天然有依据，不应被门禁阻断
            #   覆盖性: 覆盖所有降级用例（无论 _generation_source 是 rule_extract/page_merged/...）
            #   全局观: 修复后降级用例不再被门禁阻断，Step4 能执行，Step5 有报告
            #   职责边界: 在门禁层修复（Step4 启动前），不越界到 Step3
            #   自我反驳3次:
            #     1) 用 page_merged 判断? → page_merged 不一定都降级，豁免过宽
            #     2) 改 page_merged 合并保留原 source? → 改动大，违反§10.1最小粒度
            #     3) 用 downgrade_reason 非空判断? → low_confidence 更语义化且必设
            if intent.get("low_confidence") is True:
                _log.info(
                    "[Gate] 低置信度用例 '%s' 不豁免门禁 (low_confidence=True, source=%s) — 降级=失败",
                    title[:50],
                    intent.get("_generation_source", ""),
                )
                # ★ P0-downgrade-removal(2026-07-29): 不豁免，降级=失败，低置信度用例仍需通过门禁

            # 1. 校验测试用例标题+描述（关键词匹配 + 语义相似度双保险）
            intent_keywords = self._extract_keywords(title + " " + desc)
            case_match = self._check_in_requirement(intent_keywords)

            # ★ 语义相似度检查（补充关键词匹配的不足）
            semantic_match = 0.0
            if case_match < self._INTENT_BLOCK_THRESHOLD:
                with guard("check_intents: from src.extensions.semantic_index i", log=_log):
                    from src.extensions.semantic_index import similarity

                    intent_text = title + " " + desc
                    semantic_match = similarity(intent_text, self.requirement_text)
                    if (
                        semantic_match
                        >= pipeline_config.requirement_gate_intent_semantic_threshold
                    ):  # 用例级语义阈值（可配置）
                        case_match = max(case_match, semantic_match)
                        _log.info(
                            "[Gate] 测试用例 '%s' 关键词匹配低但语义相似度高 (%.2f)，放行",
                            title[:30],
                            semantic_match,
                        )

            # ★ P2-2: RAG Top-K 向量检索兜底（基础语义不达标时，在 requirement chunks 中找最相似段）
            if (
                case_match < self._INTENT_BLOCK_THRESHOLD
                and semantic_match
                < pipeline_config.requirement_gate_intent_semantic_threshold
            ):
                with guard("check_intents: from src.extensions.rag_engine impor", log=_log):
                    from src.extensions.rag_engine import rag_engine

                    intent_text = title + " " + desc
                    hits = rag_engine._vector_search(
                        intent_text, chunk_type="requirement", top_k=3
                    )
                    if hits:
                        top_score = float(hits[0].get("score", 0.0))
                        if top_score > semantic_match:
                            semantic_match = top_score
                            _log.info(
                                "[Gate] 测试用例 '%s' RAG 检索命中 (score=%.2f): %s",
                                title[:30],
                                top_score,
                                (hits[0].get("text", "") or "")[:50],
                            )
                            if (
                                semantic_match
                                >= pipeline_config.requirement_gate_intent_semantic_threshold
                            ):
                                case_match = max(case_match, semantic_match)
            intent["_semantic_score"] = semantic_match

            if case_match < self._INTENT_BLOCK_THRESHOLD:
                # 测试用例整体找不到依据 → 阻断
                result.passed = False
                result.blockers.append(
                    {
                        "case_id": case_id,
                        "reason": (
                            f"【阻断】测试用例 '{title}' 在需求文档中找不到依据。"
                            f"关键词匹配度 {case_match:.0%} < {self._INTENT_BLOCK_THRESHOLD:.0%}。"
                            f"该测试用例可能是AI幻觉生成。"
                        ),
                        "type": "hallucination",
                        "match_score": case_match,
                    }
                )
                continue  # 步骤不用检查了

            # 2. 校验每个步骤（同样双保险）
            for si, step in enumerate(steps if isinstance(steps, list) else []):
                if not isinstance(step, dict):
                    continue
                step_desc = step.get("description", "")
                step_keywords = self._extract_keywords(step_desc)
                step_match = self._check_in_requirement(step_keywords)

                # ★ 语义相似度检查
                if step_match < self._STEP_BLOCK_THRESHOLD:
                    try:
                        from src.extensions.semantic_index import similarity

                        step_semantic = similarity(step_desc, self.requirement_text)
                        if (
                            step_semantic
                            >= pipeline_config.requirement_gate_step_semantic_threshold
                        ):  # 步骤语义阈值（可通过环境变量调整）
                            step_match = max(step_match, step_semantic)
                    except Exception as _e:
                        _log.debug("check + import: %s", _e, exc_info=True)

                if step_match < self._STEP_BLOCK_THRESHOLD:
                    # 步骤找不到依据 → 阻断
                    result.passed = False
                    result.blockers.append(
                        {
                            "case_id": case_id,
                            "step": si + 1,
                            "reason": (
                                f"【阻断】步骤{si+1} '{step_desc[:50]}...' 在需求文档中找不到依据。"
                            ),
                            "type": "step_hallucination",
                            "match_score": step_match,
                        }
                    )

        if result.passed and not result.warnings:
            result.summary = f"测试用例校验通过: {len(intents)} 条测试用例均有依据"
        elif result.passed:
            result.summary = f"测试用例校验通过: {len(intents)} 条测试用例, {len(result.warnings)} 个警告"
        else:
            result.summary = (
                f"测试用例校验阻断: {len(result.blockers)} 个测试用例/步骤无依据"
            )

        return result


# ── 词表唯一源（提升自 src/extensions/quality/requirement_gate/check_mixins/_intents.py，下游 import 引用，勿重抄）──
# ★ 规则提取用例天然有依据（基于 RAG 真实页面路径 + requirements 表生成），豁免门禁
#   - rule_extract: AI 之前的规则前置提取（P2.5 新增）
#   - local_rule_fallback: AI 失败后的规则兜底
#   - cross_requirement: 跨需求关联用例，AI 组合多个需求生成，无法在单一需求文档中验证
_EXEMPT_SOURCES = (
    "rule_extract",
    "local_rule_fallback",
    "cross_requirement",
)
