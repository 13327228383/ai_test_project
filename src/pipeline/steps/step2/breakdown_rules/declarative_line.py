# -*- coding: utf-8 -*-
"""陈述句提取规则：提取阶段，从无编号/无标记的自然语言陈述句中提取原子需求。

适用场景：需求文档主体写作「## 模块 + 陈述句」——既没有 **操作步骤** 标记，
也没有编号/项目符号，也不是「当…则…」条件句式。

提取契约：
  - 只吃陈述句，标题/列表/表格/引用/分隔线等结构行交由对应规则处理；
  - 句内出现触发词（…时/后/前，…）时切成 (前置条件, 结果)，否则整句视为一个行为；
  - source_quote 保留原句，保证下游「source_quote ⊆ 原文」校验成立。
"""

import re
from typing import Any

from .base import ExtractRule
from .conditional_statement import _CONDITIONAL_PATTERN
from .predicate import has_executable_predicate
from src.pipeline.step3._rule_extractor_keywords import is_api_operation

# 结构行（由其他规则或非需求内容处理）：标题/列表/表格/引用/分隔线/整行加粗
_STRUCTURAL_LINE = re.compile(
    r"^\s*(?:#{1,6}\s|[-*+]\s+|\d+[.)、]\s*|\||>|-{3,}|\*{3,}|_{3,}|\*\*[^*\n]+\*\*\s*$)"
)

# 非系统行为的行：文档自述与版本/变更记录
_META_LINE = re.compile(r"^(?:本|该|此)(?:需求)?文档|^v?\d+(?:\.\d+)+[^\d]", re.IGNORECASE)

# 数据模型/术语定义行（"Product: id, name, ..."），英文动词会误命中其中的 update 等词
_ASCII_DEFINITION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*[:：]")

# 接口清单行（HTTP method + path）由 step3 唯一事实源 is_api_operation 判定

# 触发词切分：「…时/后/前，…」→ (前置条件, 结果)。
#   前瞻/后顾排除词内假触发：时间、然后、当前、目前、后台、前台、前提 等。
_TRIGGER_SPLIT = re.compile(
    r"^(?P<cond>.{2,60}?)"
    r"(?:时(?![间刻区段代候])|之后|以后|之前|"
    r"(?<!然)后(?![台端面续缀])|(?<![当目])前(?![台端后背景提]))"
    r"[，,：:]?\s*(?P<result>.{2,})$"
)

# 句子切分（保留句末标点，保证 source_quote 逐字存在于原文）
_SENTENCE = re.compile(r"[^。；！？\n]+[。；！？]?")

_TRAILING_PUNCT = re.compile(r"[。；！？，,：:\s]+$")

_MIN_SENTENCE_LEN = 6


def _clean_clause(text: str) -> str:
    """去掉首尾空白与句末标点。"""
    return _TRAILING_PUNCT.sub("", (text or "").strip()).strip()


def _has_predicate(text: str) -> bool:
    """句子是否陈述系统行为（委托唯一判据，避免词表分叉）。"""
    return has_executable_predicate(text)


class DeclarativeLineRule(ExtractRule):
    """从自然语言陈述句中提取原子需求。

    匹配格式：
        库存低于阈值时在列表中标红提示。
        分类名称唯一。
        支持导出当前筛选结果为 CSV。
        GET /api/products 获取商品列表。

    每条陈述句 = 一个原子需求；带触发词时切成 前置条件 + 结果。
    """

    rule_id = "declarative_line"
    name = "陈述句提取"
    description = (
        "从无编号/无标记的自然语言陈述句中提取原子需求，"
        "支持「…时/后，…」触发切分与 HTTP 接口清单行"
    )
    priority = 60

    def apply(self, chunk_text: str, chunk_id: str) -> list[dict[str, Any]]:
        if not chunk_text or not chunk_text.strip():
            return []

        triples = []
        seen = set()
        for raw_line in chunk_text.splitlines():
            line = raw_line.strip()
            if not line or _STRUCTURAL_LINE.match(line):
                continue
            if _META_LINE.match(line) or _ASCII_DEFINITION.match(line):
                continue
            for sentence in _SENTENCE.findall(line):
                sentence = sentence.strip()
                if len(sentence) < _MIN_SENTENCE_LEN or sentence in seen:
                    continue
                triple = self._sentence_to_triple(sentence, chunk_id)
                if not triple:
                    continue
                seen.add(sentence)
                triples.append(triple)
        return triples

    def _sentence_to_triple(
        self, sentence: str, chunk_id: str
    ) -> dict[str, Any] | None:
        body = _clean_clause(sentence)
        if not body:
            return None

        if is_api_operation(sentence):
            return self._build_triple(
                operation=body,
                condition="",
                expected_feedback=self._declared_or_inferred(body),
                source_quote=sentence,
                chunk_id=chunk_id,
                confidence=0.90,
            )

        # 「当…则…」句式由 conditional_statement 负责，避免同一句被两套规则重复提取
        if _CONDITIONAL_PATTERN.search(sentence):
            return None

        if not _has_predicate(body):
            return None

        m = _TRIGGER_SPLIT.match(body)
        if m:
            condition = _clean_clause(m.group("cond"))
            result = _clean_clause(m.group("result"))
        else:
            condition, result = "", ""

        if not result:
            return self._build_triple(
                operation=body,
                condition="",
                expected_feedback=self._declared_or_inferred(body),
                source_quote=sentence,
                chunk_id=chunk_id,
                confidence=0.80,
            )

        return self._build_triple(
            operation=result,
            condition=condition,
            expected_feedback=self._declared_or_inferred(result),
            source_quote=sentence,
            chunk_id=chunk_id,
            confidence=0.85,
        )

    @staticmethod
    def _declared_or_inferred(result: str) -> str:
        """陈述句/触发结果子句本身就是需求声明的期望反馈（唯一解析入口在 step3）。"""
        from src.pipeline.step3._rule_extractor_keywords import (
            resolve_expected_feedback,
        )

        return resolve_expected_feedback(result, declared=result)

    @staticmethod
    def _build_triple(
        operation: str,
        condition: str,
        expected_feedback: str,
        source_quote: str,
        chunk_id: str,
        confidence: float,
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "condition": condition,
            "expected_feedback": expected_feedback,
            "source_quote": source_quote,
            "source_section": chunk_id,
            "confidence": confidence,
            "extraction_method": "rule",
            "is_implicit": 0,
        }
